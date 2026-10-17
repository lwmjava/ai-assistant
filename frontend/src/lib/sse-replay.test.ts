import { afterEach, expect, test, vi } from 'vitest'

import { streamPost } from '@/lib/sse'

vi.mock('@/lib/http', () => ({
  API_BASE: '/api/v1',
  notifySessionExpired: vi.fn(),
  refreshTokens: vi.fn(),
}))

afterEach(() => vi.unstubAllGlobals())

const runId = '11111111-2222-4333-8444-555555555555'
const encoder = new TextEncoder()
const frame = (seq: number, event: string, fields: Record<string, unknown>) =>
  `id: ${runId}:${seq}\nevent: ${event}\ndata: ${JSON.stringify({ run_id: runId, ...fields })}\n\n`

test('SSE 帧保留事件 ID，断线只 GET 回放且不再次提交消息', async () => {
  let pulls = 0
  const disconnected = new Response(new ReadableStream({
    pull(controller) {
      if (pulls++ === 0) controller.enqueue(encoder.encode(frame(1, 'run.started', {})))
      else controller.error(new TypeError('network disconnected'))
    },
  }), { status: 200 })
  const resumed = new Response(
    frame(1, 'run.started', {}) + frame(2, 'token', { delta: '回复' }) +
    frame(3, 'run.finished', { status: 'done' }),
    { status: 200 },
  )
  const fetchMock = vi.fn().mockResolvedValueOnce(disconnected).mockResolvedValueOnce(resumed)
  vi.stubGlobal('fetch', fetchMock)
  const messages: { event: string; data: string; id?: string }[] = []
  const onError = vi.fn()
  await streamPost('/chat/stream', { message: '协议测试', run_id: runId }, null, {
    onMessage: (msg) => messages.push(msg), onError,
  })
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(fetchMock.mock.calls[1]).toEqual([
    `/api/v1/runs/${runId}/events`,
    expect.objectContaining({ method: 'GET', headers: expect.objectContaining({
      'Last-Event-ID': `${runId}:1`,
    }) }),
  ])
  expect(messages.map((msg) => msg.id)).toEqual([`${runId}:1`, `${runId}:2`, `${runId}:3`])
  expect(messages.filter((msg) => msg.event === 'run.started')).toHaveLength(1)
  expect(onError).not.toHaveBeenCalled()
})

test('正常 EOF 缺少终态时也只重连一次，第二次断开返回错误', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(frame(1, 'run.started', {})))
    .mockResolvedValueOnce(new Response(frame(2, 'heartbeat', { ts: 'fixture' })))
  vi.stubGlobal('fetch', fetchMock)
  const onError = vi.fn()
  await streamPost('/chat/stream', { message: '协议测试', run_id: runId }, null, {
    onMessage: vi.fn(), onError,
  })
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(onError).toHaveBeenCalledTimes(1)
})

test('页面回调失败结束读取，不当作网络故障重复回放', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(frame(1, 'run.started', {})))
  vi.stubGlobal('fetch', fetchMock)
  const onError = vi.fn()
  await streamPost('/chat/stream', { run_id: runId }, null, {
    onMessage: () => { throw new TypeError('synthetic UI callback failure') }, onError,
  })
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(onError).toHaveBeenCalledTimes(1)
})
