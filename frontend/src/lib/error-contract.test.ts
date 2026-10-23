import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { api, setSessionExpiredHandler } from './http'
import { retryAfterSeconds } from './rate-limit'
import { streamPost } from './sse'
import { clearSessionNotice, useSessionNotice } from './session-notice'
import { ERROR_MESSAGES } from './api-errors'
import { invalidateAuthSession } from './auth-session'

let stored: string | null
const fetchMock = vi.fn<typeof fetch>()
const expired = vi.fn()
const token = () => `h.${btoa(JSON.stringify({ exp: Date.now() / 1000 + 3600 }))}.synthetic`
const failure = (code: string, status: number, extra = {}) => new Response(JSON.stringify({
  detail: 'legacy detail', error: { code, message: 'backend detail', request_id: 'PRIVATE-REQUEST-ID', retry_after: null, ...extra },
}), { status })

beforeEach(() => {
  stored = JSON.stringify({ state: { access_token: token(), refresh_token: 'synthetic-refresh' } })
  fetchMock.mockReset(); expired.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('localStorage', { getItem: () => stored, setItem: (_: string, value: string) => { stored = value }, removeItem: () => { stored = null } })
  setSessionExpiredHandler(expired)
  clearSessionNotice()
})
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

test.each([
  ['token_revoked', 401, '您的登录凭证已被管理员重置，请重新登录'],
  ['user_disabled', 403, '用户已被禁用，请联系管理员'],
  ['tenant_suspended', 403, '租户已停用，请联系系统管理员'],
])('terminal %s preserves cause and never refreshes', async (code, status, detail) => {
  fetchMock.mockResolvedValueOnce(failure(code, status))
  await expect(api.get('/auth/me')).rejects.toMatchObject({ code, detail, sessionExpired: true })
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(stored).toBeNull()
  expect(expired).toHaveBeenCalledWith(expect.objectContaining({ code, detail }))
})

test('refresh rejection retains revoked cause instead of replacing with generic expiry', async () => {
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 })).mockResolvedValueOnce(failure('token_revoked', 401))
  await expect(api.get('/auth/me')).rejects.toMatchObject({ code: 'token_revoked' })
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

test('second 401 ends session after one refresh and one replay', async () => {
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: token(), refresh_token: 'next-refresh' })))
    .mockResolvedValueOnce(failure('session_expired', 401))
  await expect(api.get('/auth/me')).rejects.toMatchObject({ code: 'session_expired', sessionExpired: true })
  expect(fetchMock).toHaveBeenCalledTimes(3)
  expect(stored).toBeNull()
})

test('concurrent expired access refreshes once and returns silently', async () => {
  let resolve!: (value: Response) => void
  const refreshed = `${token()}.next`
  fetchMock.mockImplementation(async (url, options) => {
    if (String(url).endsWith('/auth/refresh')) return new Promise<Response>((r) => { resolve = r })
    return new Headers(options?.headers).get('Authorization') === `Bearer ${refreshed}`
      ? new Response('{"ok":true}') : new Response('{}', { status: 401 })
  })
  const requests = Promise.all([api.get('/one'), api.get('/two')])
  await vi.waitFor(() => expect(resolve).toBeTypeOf('function'))
  resolve(new Response(JSON.stringify({ access_token: refreshed, refresh_token: 'next-refresh' })))
  await expect(requests).resolves.toEqual([{ ok: true }, { ok: true }])
  expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/auth/refresh'))).toHaveLength(1)
  expect(expired).not.toHaveBeenCalled()
})

test.each(['quota_exceeded', 'model_unavailable', 'file_too_large'] as const)('canonical %s is parsed without exposing request id', async (code) => {
  fetchMock.mockResolvedValueOnce(failure(code, 429))
  await expect(api.get('/test')).rejects.toMatchObject({ code, detail: ERROR_MESSAGES[code] })
})

test.each([NaN, Infinity, -1, 0, Number.MAX_VALUE])('unsafe retry_after %s never creates an unbounded countdown', (value) => {
  expect(retryAfterSeconds({ error: { code: 'rate_limited', retry_after: value } })).toBeNull()
})
test('canonical retry_after uses ceiling seconds', () => {
  expect(retryAfterSeconds({ error: { code: 'rate_limited', retry_after: 1.2 } })).toBe(2)
})

test('file limit is visible without arbitrary backend prose or request identity', async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'file_too_large', message: 'internal failure', request_id: 'PRIVATE' }, limit_bytes: 1048576 }), { status: 413 }))
  await expect(api.get('/test')).rejects.toMatchObject({ detail: '上传文件超过上限（上限 1048576 字节）' })
})

test('legacy quota payload remains readable', async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ code: 'quota_exceeded', limit_type: 'messages', used: 10, limit: 10 }), { status: 429 }))
  await expect(api.get('/test')).rejects.toMatchObject({
    code: 'quota_exceeded',
    detail: '消息条数已达上限：本租户已有 10 条用户消息，上限是 10 条。请联系管理员提高上限，或删除部分会话后再发送。',
  })
})

test('token quota envelope shows usage, limit and reset without request id', async () => {
  const resetsAt = '2026-11-01T00:00:00Z'
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
    code: 'quota_exceeded', limit_type: 'tokens', used: 1, limit: 1, resets_at: resetsAt,
    error: { code: 'quota_exceeded', message: '本租户配额已用尽，请联系租户管理员', request_id: 'PRIVATE-REQUEST-ID', retry_after: null },
  }), { status: 429 }))
  const error = await api.get('/test').then(() => null, (err: { detail?: string; code?: string }) => err)
  expect(error).toMatchObject({ code: 'quota_exceeded' })
  expect(error?.detail).toBe(`Token 已达上限：本月已用 1，上限是 1。下次重置时间：${resetsAt}。请联系管理员提高上限，或等待自然月重置。`)
  expect(error?.detail).not.toContain('PRIVATE-REQUEST-ID')
})

test('tenant suspension cause survives clearing tokens and changing route', async () => {
  fetchMock.mockResolvedValueOnce(failure('tenant_suspended', 403))
  await expect(api.get('/auth/me')).rejects.toMatchObject({ sessionExpired: true })
  expect(stored).toBeNull()
  expect(useSessionNotice.getState()).toMatchObject({ message: ERROR_MESSAGES.tenant_suspended, suspended: true })
  useSessionNotice.setState({ suspended: false })
  expect(useSessionNotice.getState().message).toBe(ERROR_MESSAGES.tenant_suspended)
})

const run = '11111111222243338444555555555555'
const frame = (seq: number, event: string, fields = {}) => `id: ${run}:${seq}\nevent: ${event}\ndata: ${JSON.stringify({ run_id: run, ...fields })}\n\n`

test('stream token quota rejection uses the same usage notice', async () => {
  const resetsAt = '2026-11-01T00:00:00Z'
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
    code: 'quota_exceeded', limit_type: 'tokens', used: 1, limit: 1, resets_at: resetsAt,
    error: { code: 'quota_exceeded', message: 'backend detail', request_id: 'PRIVATE-REQUEST-ID', retry_after: null },
  }), { status: 429 }))
  const onError = vi.fn()
  await streamPost('/chat/stream', { run_id: run }, token(), { onMessage: vi.fn(), onError })
  const message = String(onError.mock.calls[0]?.[0]?.message ?? '')
  expect(message).toContain('Token 已达上限')
  expect(message).toContain('本月已用 1')
  expect(message).toContain('上限是 1')
  expect(message).toContain(`下次重置时间：${resetsAt}`)
  expect(message).not.toContain('PRIVATE-REQUEST-ID')
})

test.each(['token_revoked', 'user_disabled', 'tenant_suspended'])('SSE HTTP %s never refreshes terminal identity', async (code) => {
  fetchMock.mockResolvedValueOnce(failure(code, code === 'token_revoked' ? 401 : 403))
  const onError = vi.fn()
  await streamPost('/chat/stream', { run_id: run }, token(), { onMessage: vi.fn(), onError })
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(onError).toHaveBeenCalledWith(expect.objectContaining({ code }))
  expect(stored).toBeNull()
})

test('SSE refresh failure keeps revoked reason', async () => {
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 })).mockResolvedValueOnce(failure('token_revoked', 401))
  const onError = vi.fn()
  await streamPost('/chat/stream', { run_id: run }, token(), { onMessage: vi.fn(), onError })
  expect(onError).toHaveBeenCalledWith(expect.objectContaining({ code: 'token_revoked', message: ERROR_MESSAGES.token_revoked }))
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

test('SSE HTTP rate limit preserves canonical payload for countdown consumer', async () => {
  fetchMock.mockResolvedValueOnce(failure('rate_limited', 429, { retry_after: 3 }))
  const onError = vi.fn()
  await streamPost('/chat/stream', { run_id: run }, token(), { onMessage: vi.fn(), onError })
  expect(retryAfterSeconds(onError.mock.calls[0]?.[0].payload)).toBe(3)
})

test('SSE terminal identity event clears session without replaying generation', async () => {
  fetchMock.mockResolvedValueOnce(new Response(frame(1, 'error', { error: { code: 'tenant_suspended' } }) + frame(2, 'run.finished', { status: 'failed' })))
  await streamPost('/chat/stream', { run_id: run }, token(), { onMessage: vi.fn(), onError: vi.fn() })
  expect(stored).toBeNull()
  expect(useSessionNotice.getState().suspended).toBe(true)
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

test('anonymous disabled-user login rejects with reason without refresh', async () => {
  fetchMock.mockResolvedValueOnce(failure('user_disabled', 403))
  await expect(api.post('/auth/login', { username: 'synthetic', password: 'synthetic' }, { anonymous: true })).rejects.toMatchObject({ code: 'user_disabled', detail: ERROR_MESSAGES.user_disabled })
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

test('refresh timeout is bounded and clears session without replay', async () => {
  vi.useFakeTimers()
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 })).mockImplementationOnce(() => new Promise(() => {}))
  const result = api.get('/auth/me')
  const rejected = expect(result).rejects.toMatchObject({ code: 'session_expired', sessionExpired: true })
  await vi.advanceTimersByTimeAsync(15_001)
  await rejected
  expect(stored).toBeNull()
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect((fetchMock.mock.calls[1]?.[1]?.signal as AbortSignal).aborted).toBe(true)
})

test.each(['logout', 'account-switch'])('inflight refresh cannot revive or overwrite session after %s', async (action) => {
  let resolve!: (value: Response) => void
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 })).mockImplementationOnce(() => new Promise((r) => { resolve = r }))
  const pending = api.get('/auth/me')
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AuthSessionChangedError' })
  await vi.waitFor(() => expect(resolve).toBeTypeOf('function'))
  const accountB = JSON.stringify({ state: { access_token: 'synthetic-account-b', refresh_token: 'synthetic-refresh-b' } })
  stored = action === 'logout' ? null : accountB
  resolve(new Response(JSON.stringify({ access_token: token(), refresh_token: 'stale-account-a' })))
  await rejected
  expect(stored).toBe(action === 'logout' ? null : accountB)
  expect(expired).not.toHaveBeenCalled()
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

test('old refresh rejection cannot clear a newer account', async () => {
  let resolve!: (value: Response) => void
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 })).mockImplementationOnce(() => new Promise((r) => { resolve = r }))
  const pending = api.get('/auth/me')
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AuthSessionChangedError' })
  await vi.waitFor(() => expect(resolve).toBeTypeOf('function'))
  const accountB = JSON.stringify({ state: { access_token: 'account-b', refresh_token: 'refresh-b' } })
  stored = accountB
  resolve(failure('token_revoked', 401))
  await rejected
  expect(stored).toBe(accountB)
  expect(expired).not.toHaveBeenCalled()
})

test('refresh body silence is bounded after successful response headers', async () => {
  vi.useFakeTimers()
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 })).mockResolvedValueOnce(new Response(new ReadableStream({ start() { /* never produces body */ } })))
  const pending = api.get('/auth/me')
  let settled = false
  void pending.then(() => { settled = true }, () => { settled = true })
  const rejected = expect(pending).rejects.toMatchObject({ code: 'session_expired', sessionExpired: true })
  await vi.advanceTimersByTimeAsync(15_001)
  expect(settled).toBe(true)
  await rejected
})

test('old business 401 after a newer login cannot refresh or clear that account', async () => {
  let resolve!: (value: Response) => void
  fetchMock.mockImplementationOnce(() => new Promise((r) => { resolve = r }))
  const pending = api.get('/auth/me')
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AuthSessionChangedError' })
  await vi.waitFor(() => expect(resolve).toBeTypeOf('function'))
  invalidateAuthSession()
  const accountB = JSON.stringify({ state: { access_token: 'account-b', refresh_token: 'refresh-b' } })
  stored = accountB
  resolve(failure('token_revoked', 401))
  await rejected
  expect(stored).toBe(accountB)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(expired).not.toHaveBeenCalled()
})

test('old SSE forbidden response after account switch cannot expire new session', async () => {
  let resolve!: (value: Response) => void
  fetchMock.mockImplementationOnce(() => new Promise((r) => { resolve = r }))
  const onError = vi.fn()
  const pending = streamPost('/chat/stream', { run_id: run }, token(), { onMessage: vi.fn(), onError })
  invalidateAuthSession()
  const accountB = JSON.stringify({ state: { access_token: 'account-b', refresh_token: 'refresh-b' } })
  stored = accountB
  resolve(failure('tenant_suspended', 403))
  await pending
  expect(stored).toBe(accountB)
  expect(expired).not.toHaveBeenCalled()
  expect(onError).toHaveBeenCalledWith(expect.objectContaining({ name: 'AuthSessionChangedError' }))
})
