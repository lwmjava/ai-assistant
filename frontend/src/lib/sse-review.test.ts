import { afterEach, expect, test, vi } from 'vitest'

import { refreshTokens } from '@/lib/http'
import { streamPost, StreamInterruptedError } from '@/lib/sse'

vi.mock('@/lib/http', () => ({ API_BASE: '/api/v1', notifySessionExpired: vi.fn(), refreshTokens: vi.fn() }))

const identity = '11111111222243338444555555555555'
const other = 'aaaaaaaa222243338444555555555555'
const frame = (sequence: number, event: string, fields: Record<string, unknown> = {}) =>
  `id: ${identity}:${sequence}\nevent: ${event}\ndata: ${JSON.stringify({ run_id: identity, ...fields })}\n\n`

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  vi.clearAllMocks()
})

test('sequence gaps cannot silently discard events and report successful completion', async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(
    frame(1, 'run.started') + frame(3, 'token', { delta: 'after gap' }) + frame(4, 'run.finished', { status: 'done' }),
  )).mockResolvedValueOnce(new Response(
    frame(2, 'token', { delta: 'missing' }) + frame(3, 'token', { delta: 'after gap' }) + frame(4, 'run.finished', { status: 'done' }),
  ))
  vi.stubGlobal('fetch', fetchMock)
  const received: number[] = []
  const onError = vi.fn()
  await streamPost('/chat/stream', { run_id: identity }, null, {
    onMessage: (message) => { received.push(Number(message.id?.split(':').at(-1))) }, onError,
  })
  expect(received.every((sequence, index) => sequence === index + 1)).toBe(true)
  expect(fetchMock.mock.calls.length > 1 || onError.mock.calls.length > 0).toBe(true)
})

test('missing event IDs cannot append unreplayable token data', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
    frame(1, 'run.started') + `event: token\ndata: {"run_id":"${identity}","delta":"untracked"}\n\n` +
    frame(2, 'run.finished', { status: 'done' }),
  )))
  const onMessage = vi.fn()
  await streamPost('/chat/stream', { run_id: identity }, null, { onMessage, onError: vi.fn() })
  expect(onMessage.mock.calls.some(([message]) => message.event === 'token')).toBe(false)
})

test('a mismatched payload run cannot terminate the current run', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
    frame(1, 'run.started') + frame(2, 'run.finished', { run_id: other, status: 'done' }),
  )))
  const onMessage = vi.fn()
  const onError = vi.fn()
  await streamPost('/chat/stream', { run_id: identity }, null, { onMessage, onError })
  expect(onMessage.mock.calls.some(([message]) => message.event === 'run.finished')).toBe(false)
  expect(onError).toHaveBeenCalled()
})

test('replay 401 refreshes credentials and retries only the existing GET subscription', async () => {
  vi.mocked(refreshTokens).mockResolvedValue({ access_token: 'next-access', refresh_token: 'next-refresh' })
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(frame(1, 'run.started')))
    .mockResolvedValueOnce(new Response('{}', { status: 401 }))
    .mockResolvedValueOnce(new Response(frame(2, 'run.finished', { status: 'done' })))
  vi.stubGlobal('fetch', fetchMock)
  const onError = vi.fn()
  await streamPost('/chat/stream', { run_id: identity }, 'first-access', { onMessage: vi.fn(), onError })
  expect(fetchMock.mock.calls.map(([url, options]) => [url, options.method])).toEqual([
    ['/api/v1/chat/stream', 'POST'], [`/api/v1/runs/${identity}/events`, 'GET'],
    [`/api/v1/runs/${identity}/events`, 'GET'],
  ])
  expect(fetchMock.mock.calls[2]?.[1].headers).toMatchObject({
    Authorization: 'Bearer next-access', 'Last-Event-ID': `${identity}:1`,
  })
  expect(onError).not.toHaveBeenCalled()
})

test('expired replay window reports interruption so final result can be loaded', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(frame(1, 'run.started')))
    .mockResolvedValueOnce(new Response('{}', { status: 409 })))
  const onError = vi.fn()
  await streamPost('/chat/stream', { run_id: identity }, null, { onMessage: vi.fn(), onError })
  expect(onError).toHaveBeenCalledWith(expect.any(StreamInterruptedError))
})

test('heartbeat silence cancels the old reader and opens one GET replay', async () => {
  vi.useFakeTimers()
  const cancelled = vi.fn()
  const encoder = new TextEncoder()
  const initial = new Response(new ReadableStream({
    start(controller) { controller.enqueue(encoder.encode(frame(1, 'run.started'))) }, cancel: cancelled,
  }))
  const fetchMock = vi.fn().mockResolvedValueOnce(initial)
    .mockResolvedValueOnce(new Response(frame(2, 'run.finished', { status: 'done' })))
  vi.stubGlobal('fetch', fetchMock)
  const pending = streamPost('/chat/stream', { run_id: identity }, null, { onMessage: vi.fn(), onError: vi.fn() })
  await vi.advanceTimersByTimeAsync(30_001)
  await pending
  expect(cancelled).toHaveBeenCalledTimes(1)
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(fetchMock.mock.calls[1]?.[1].method).toBe('GET')
})

test('waiting for initial response headers is bounded and resumes without a second POST', async () => {
  vi.useFakeTimers()
  let initialSignal: AbortSignal | undefined
  const fetchMock = vi.fn().mockImplementationOnce((_url, options) => {
    initialSignal = options.signal
    return new Promise((_resolve, reject) => {
      initialSignal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })
  }).mockResolvedValueOnce(new Response(frame(1, 'run.started') + frame(2, 'run.finished', { status: 'done' })))
  vi.stubGlobal('fetch', fetchMock)
  const onError = vi.fn()
  const pending = streamPost('/chat/stream', { run_id: identity }, null, { onMessage: vi.fn(), onError })
  await vi.advanceTimersByTimeAsync(30_001)
  await pending
  expect(initialSignal?.aborted).toBe(true)
  expect(fetchMock.mock.calls.map(([, options]) => options.method)).toEqual(['POST', 'GET'])
  expect(onError).not.toHaveBeenCalled()
})
