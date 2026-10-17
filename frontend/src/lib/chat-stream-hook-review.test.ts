import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { useChatStream } from '@/hooks/useChatStream'
import type { StreamSnapshot } from '@/hooks/useChatStream'
import { api } from '@/lib/http'
import { streamPost } from '@/lib/sse'

// 确定性 hook 宿主只模拟状态提交和卸载，不把它称为浏览器交互验收。
const host = vi.hoisted(() => ({ state: undefined as unknown, cleanups: [] as (() => void)[] }))

vi.mock('react', () => ({
  useState: (initial: unknown) => {
    host.state = typeof initial === 'function' ? (initial as () => unknown)() : initial
    return [host.state, (next: unknown) => {
      host.state = typeof next === 'function' ? (next as (previous: unknown) => unknown)(host.state) : next
    }]
  },
  useRef: (current: unknown) => ({ current }),
  useCallback: (callback: unknown) => callback,
  useEffect: (effect: () => void | (() => void)) => {
    const cleanup = effect()
    if (typeof cleanup === 'function') host.cleanups.push(cleanup)
  },
}))
vi.mock('@/lib/http', () => ({ api: { post: vi.fn() }, hideStackTrace: (value: string) => value }))
vi.mock('@/lib/sse', () => ({ streamPost: vi.fn(), StreamInterruptedError: class extends Error {} }))
vi.mock('@/store/auth', () => ({ getAccessToken: () => 'synthetic-session' }))

const firstId = '11111111-2222-4333-8444-555555555555'
const nextId = 'aaaaaaaa-2222-4333-8444-555555555555'
const snapshot = () => host.state as StreamSnapshot
const deferred = () => {
  let resolve!: () => void
  const promise = new Promise<void>((complete) => { resolve = complete })
  return { promise, resolve }
}

beforeEach(() => {
  host.state = undefined
  host.cleanups = []
  vi.mocked(streamPost).mockReset()
  vi.mocked(api.post).mockReset()
  vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce(firstId).mockReturnValueOnce(nextId) })
})
afterEach(() => {
  host.cleanups.forEach((cleanup) => cleanup())
  vi.unstubAllGlobals()
})

test('late completion of an aborted old send cannot fail the next run', async () => {
  const old = deferred()
  const current = deferred()
  vi.mocked(streamPost).mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
  const hook = useChatStream()
  const first = hook.send({ message: 'first', conversationId: null })
  const second = hook.send({ message: 'next', conversationId: null })
  old.resolve()
  await first
  expect(snapshot()).toMatchObject({ runId: nextId, streaming: true, error: null })
  current.resolve()
  await second
})

test('reset cannot be undone by the old stream returning later', async () => {
  const old = deferred()
  vi.mocked(streamPost).mockReturnValueOnce(old.promise)
  const hook = useChatStream()
  const pending = hook.send({ message: 'first', conversationId: null })
  hook.reset()
  old.resolve()
  await pending
  expect(snapshot()).toMatchObject({ runId: null, streaming: false, error: null, text: '' })
})

test('stop before any metadata uses the preallocated ID and waits for cancellation acknowledgement', async () => {
  const stream = deferred()
  const cancel = deferred()
  vi.mocked(streamPost).mockReturnValueOnce(stream.promise)
  const result = { status: 'cancelled', conversation_id: 'conversation', assistant_message_id: 'partial' }
  vi.mocked(api.post).mockReturnValueOnce(cancel.promise.then(() => result))
  const hook = useChatStream()
  const sending = hook.send({ message: 'first', conversationId: null })
  let acknowledged = false
  const stopping = Promise.resolve(hook.stop()).then((value) => { acknowledged = true; return value })
  await Promise.resolve()
  expect(api.post).toHaveBeenCalledWith(`/runs/${firstId}/cancel`)
  expect(acknowledged).toBe(false)
  cancel.resolve()
  await expect(stopping).resolves.toEqual(result)
  stream.resolve()
  await sending
  expect(snapshot().stoppedByUser).toBe(true)
})

test('terminal callback fires once with text from the current run only', async () => {
  vi.mocked(streamPost).mockImplementationOnce(async (_path, body, _token, options) => {
    const identity = (body as { run_id: string }).run_id
    options.onMessage({ event: 'token', data: JSON.stringify({ run_id: identity, delta: 'answer' }) })
    options.onMessage({ event: 'run.finished', data: JSON.stringify({ run_id: nextId, status: 'done' }) })
    options.onMessage({ event: 'run.finished', data: JSON.stringify({ run_id: identity, status: 'done' }) })
  })
  const onFinished = vi.fn()
  const hook = useChatStream()
  await hook.send({ message: 'first', conversationId: null, onFinished })
  expect(onFinished).toHaveBeenCalledTimes(1)
  expect(onFinished).toHaveBeenCalledWith('answer', 'done')
  expect(snapshot()).toMatchObject({ streaming: false, runId: firstId, text: 'answer' })
})

test('the current stream request carries its selected attachment snapshot', async () => {
  vi.mocked(streamPost).mockResolvedValueOnce(undefined)
  const hook = useChatStream()
  const request = { message: 'question', conversationId: null,
    attachments: [{ name: 'notes.md', content_base64: 'c3ludGhldGlj' }] }
  await hook.send(request)
  expect(vi.mocked(streamPost).mock.calls[0]?.[1]).toMatchObject({ attachments: request.attachments })
})
