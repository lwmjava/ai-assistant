import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { downloadTenantConversations } from '@/api/export'
import { api } from '@/lib/http'
import { streamPost } from '@/lib/sse'

vi.mock('@/store/auth', () => ({ getAccessToken: () => 'synthetic-session' }))

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => vi.unstubAllGlobals())

test('ordinary requests use the versioned API prefix', async () => {
  fetchMock.mockResolvedValue(new Response('{"status":"ok"}', { status: 200 }))
  await api.get('/health', { anonymous: true })
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/health', expect.any(Object))
})

test('stream requests use the versioned prefix and retain authentication', async () => {
  const runId = '11111111222243338444555555555555'
  const payload = JSON.stringify({ run_id: runId, status: 'done' })
  fetchMock.mockResolvedValue(new Response(`id: ${runId}:1\nevent: run.finished\ndata: ${payload}\n\n`, { status: 200 }))
  const onMessage = vi.fn()
  await streamPost('/chat/stream', { message: 'synthetic input' }, 'synthetic-session', { onMessage })
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/chat/stream', expect.objectContaining({
    method: 'POST',
    headers: expect.objectContaining({ Authorization: 'Bearer synthetic-session' }),
  }))
  expect(onMessage).toHaveBeenCalledWith({ event: 'run.finished', data: payload, id: `${runId}:1` })
})

test('the independent export request also uses the versioned prefix', async () => {
  fetchMock.mockResolvedValue(new Response('{}', { status: 403 }))
  await expect(downloadTenantConversations()).rejects.toThrow()
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/export/conversations', expect.objectContaining({
    method: 'POST',
    headers: { Authorization: 'Bearer synthetic-session' },
  }))
})
