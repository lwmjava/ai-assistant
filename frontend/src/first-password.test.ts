import * as React from 'react'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import App from './App'
import { useAuthStore } from './store/auth'
import { api } from './lib/http'

vi.mock('./lib/http', () => ({
  api: { get: vi.fn(), post: vi.fn() },
  ApiError: class extends Error {},
  hideStackTrace: (value: string) => value,
}))
vi.mock('./components/layout/AppLayout', () => ({
  AppLayout: () => createElement('div', null, 'BUSINESS_LAYOUT'),
}))
vi.mock('./store/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./store/auth')>()
  const store = actual.useAuthStore
  return { ...actual, useAuthStore: Object.assign(
    (selector: (state: ReturnType<typeof store.getState>) => unknown) => selector(store.getState()),
    store,
  ) }
})

const user = {
  id: 'u', tenant_id: 't', username: 'culture', email: null,
  role: 'member' as const, is_active: true, must_change_password: true, is_test_user: true,
}

beforeEach(() => {
  // 既有 Vitest 配置使用 classic JSX；仅在测试宿主提供 React。
  vi.stubGlobal('React', React)
  vi.clearAllMocks()
  useAuthStore.setState({ access_token: 'access', refresh_token: 'refresh', user, bootstrapped: true })
})

function render(path: string) {
  return renderToString(createElement(MemoryRouter, { initialEntries: [path] }, createElement(App)))
}

test('待改密用户深链不能渲染业务布局', () => {
  expect(render('/chat')).not.toContain('BUSINESS_LAYOUT')
})

test('刷新时旧用户摘要不能在真实 me 返回前放行业务', () => {
  useAuthStore.setState({ user: { ...user, must_change_password: false }, bootstrapped: false })
  expect(render('/knowledge')).not.toContain('BUSINESS_LAYOUT')
})

test('首次改密页面可访问且没有业务布局', () => {
  const html = render('/change-password')
  expect(html).toContain('首次登录请修改密码')
  expect(html).not.toContain('BUSINESS_LAYOUT')
})

test('成功改密后用新双令牌重新读取用户', async () => {
  vi.mocked(api.post).mockResolvedValue({ access_token: 'new-access', refresh_token: 'new-refresh', token_type: 'bearer' })
  vi.mocked(api.get).mockResolvedValue({ ...user, must_change_password: false })
  const action = useAuthStore.getState() as unknown as { changePassword: (value: { current_password: string; new_password: string }) => Promise<void> }
  await action.changePassword({ current_password: 'current-example', new_password: 'new-example' })
  expect(api.post).toHaveBeenCalledWith('/auth/change-password', { current_password: 'current-example', new_password: 'new-example' })
  expect(api.get).toHaveBeenCalledWith('/auth/me')
  expect(useAuthStore.getState().access_token).toBe('new-access')
  expect(useAuthStore.getState().user?.must_change_password).toBe(false)
})

test('me 失败时不以持久化旧摘要放行业务', async () => {
  vi.mocked(api.get).mockRejectedValue(new Error('offline'))
  await useAuthStore.getState().loadMe()
  expect(useAuthStore.getState().user).toBeNull()
  expect(render('/chat')).not.toContain('BUSINESS_LAYOUT')
})

test('密码错误时保留待改密状态和原会话', async () => {
  vi.mocked(api.post).mockRejectedValue(new Error('current password rejected'))
  await expect(useAuthStore.getState().changePassword({ current_password: 'wrong-example', new_password: 'new-example' })).rejects.toThrow('current password rejected')
  expect(useAuthStore.getState().user?.must_change_password).toBe(true)
  expect(useAuthStore.getState().access_token).toBe('access')
  expect(api.get).not.toHaveBeenCalled()
})

test('换发令牌后的真实 me 返回前不能使用旧摘要', async () => {
  let resolveMe!: (value: typeof user) => void
  vi.mocked(api.post).mockResolvedValue({ access_token: 'new-access', refresh_token: 'new-refresh', token_type: 'bearer' })
  vi.mocked(api.get).mockImplementation(() => new Promise((resolve) => { resolveMe = resolve }))
  const pending = useAuthStore.getState().changePassword({ current_password: 'current-example', new_password: 'new-example' })
  await vi.waitFor(() => expect(useAuthStore.getState().access_token).toBe('new-access'))
  expect(useAuthStore.getState().user).toBeNull()
  expect(useAuthStore.getState().bootstrapped).toBe(false)
  expect(render('/chat')).not.toContain('BUSINESS_LAYOUT')
  resolveMe({ ...user, must_change_password: false })
  await pending
  expect(render('/chat')).toContain('BUSINESS_LAYOUT')
})

test('未登录者不能看到改密表单', () => {
  useAuthStore.setState({ access_token: null, refresh_token: null, user: null })
  expect(render('/change-password')).not.toContain('首次登录请修改密码')
})

test('旧 me 响应不能覆盖后建立的待改密会话', async () => {
  let resolveOld!: (value: typeof user) => void
  vi.mocked(api.get).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
  const oldRequest = useAuthStore.getState().loadMe()
  vi.mocked(api.get).mockResolvedValueOnce(user)
  await useAuthStore.getState().establish({ access_token: 'next-user', refresh_token: 'next-refresh', token_type: 'bearer' })
  resolveOld({ ...user, id: 'old-user', must_change_password: false })
  await oldRequest
  expect(useAuthStore.getState().user?.must_change_password).toBe(true)
  expect(render('/chat')).not.toContain('BUSINESS_LAYOUT')
})

test.each(['logout', 'reset', 'establish'] as const)('改密 POST 等待期间 %s 后旧响应不能重建旧会话', async (action) => {
  let resolvePost!: (value: { access_token: string; refresh_token: string; token_type: string }) => void
  vi.mocked(api.post).mockImplementationOnce(() => new Promise((resolve) => { resolvePost = resolve }))
  const pending = useAuthStore.getState().changePassword({ current_password: 'current-example', new_password: 'new-example' })
  if (action === 'establish') {
    vi.mocked(api.get).mockResolvedValueOnce({ ...user, id: 'next-user', must_change_password: true })
    await useAuthStore.getState().establish({ access_token: 'next-access', refresh_token: 'next-refresh', token_type: 'bearer' })
  } else {
    useAuthStore.getState()[action]()
  }
  const expected = useAuthStore.getState()
  vi.mocked(api.get).mockResolvedValueOnce({ ...user, must_change_password: false })
  resolvePost({ access_token: 'stale-access', refresh_token: 'stale-refresh', token_type: 'bearer' })
  await pending
  expect(useAuthStore.getState().access_token).toBe(expected.access_token)
  expect(useAuthStore.getState().refresh_token).toBe(expected.refresh_token)
  expect(useAuthStore.getState().user).toEqual(expected.user)
})
