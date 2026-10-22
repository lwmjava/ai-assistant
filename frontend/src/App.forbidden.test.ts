import * as React from 'react'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { MemoryRouter, Outlet } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import App from './App'
import { useAuthStore } from './store/auth'
import type { Role } from './types/api'

vi.mock('./lib/http', () => ({
  api: { get: vi.fn(), post: vi.fn() },
  ApiError: class extends Error {},
  hideStackTrace: (value: string) => value,
}))
vi.mock('./components/layout/AppLayout', () => ({
  AppLayout: () => createElement('div', null, ['LAYOUT', createElement(Outlet)]),
}))
vi.mock('./store/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./store/auth')>()
  const store = actual.useAuthStore
  return {
    ...actual,
    useAuthStore: Object.assign(
      (selector: (state: ReturnType<typeof store.getState>) => unknown) => selector(store.getState()),
      store,
    ),
  }
})

function setRole(role: Role) {
  useAuthStore.setState({
    access_token: 'access',
    refresh_token: 'refresh',
    user: {
      id: 'u',
      tenant_id: 't',
      username: 'tester',
      email: null,
      role,
      is_active: true,
      must_change_password: false,
      is_test_user: true,
    },
    bootstrapped: true,
  })
}

function render(path: string) {
  return renderToString(createElement(MemoryRouter, { initialEntries: [path] }, createElement(App)))
}

beforeEach(() => {
  vi.stubGlobal('React', React)
  setRole('member')
})

test('member opening admin pages sees 403 rather than an in-page empty hint only', () => {
  for (const path of ['/users', '/users/new', '/tenants', '/tenants/new', '/status', '/audit']) {
    const html = render(path)
    expect(html, path).toContain('403')
    expect(html, path).toContain('返回首页')
    expect(html, path).toContain('LAYOUT')
  }
})

test('viewer without agent permission sees 403 instead of being sent to chat', () => {
  setRole('viewer')
  const html = render('/tools')
  expect(html).toContain('403')
  expect(html).toContain('返回首页')
  expect(html).toContain('href="/"')
})

test('viewer cannot open chat after conversation read matrix alignment', () => {
  setRole('viewer')
  const html = render('/chat')
  expect(html).toContain('403')
  expect(html).toContain('返回首页')
})

test('system viewer can open audit but not user administration', () => {
  setRole('system_viewer')
  expect(render('/audit')).not.toContain('>403<')
  const users = render('/users')
  expect(users).toContain('403')
  expect(users).toContain('返回首页')
})

test('system admin does not get a 403 on user administration', () => {
  setRole('system_admin')
  expect(render('/users')).not.toContain('>403<')
})

test('member can still open chat without a 403 page', () => {
  expect(render('/chat')).not.toContain('>403<')
})
