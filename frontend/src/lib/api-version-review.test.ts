import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { api } from './http'

const fetchMock = vi.fn<typeof fetch>()
let stored: string | null

function syntheticToken(signature: string) {
  return `header.${btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 }))}.${signature}`
}

beforeEach(() => {
  stored = JSON.stringify({ state: { access_token: syntheticToken('first'), refresh_token: 'synthetic-refresh' } })
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('localStorage', {
    getItem: () => stored,
    setItem: (_key: string, value: string) => { stored = value },
    removeItem: () => { stored = null },
  })
})

afterEach(() => vi.unstubAllGlobals())

test('401 refresh and authenticated replay remain on the same versioned API', async () => {
  const next = syntheticToken('next')
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: next, refresh_token: 'next-refresh' })))
    .mockResolvedValueOnce(new Response('{"must_change_password":true}'))
  await expect(api.get('/auth/me')).resolves.toEqual({ must_change_password: true })
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    '/api/v1/auth/me', '/api/v1/auth/refresh', '/api/v1/auth/me',
  ])
  expect(new Headers(fetchMock.mock.calls[2]?.[1]?.headers).get('Authorization')).toBe(`Bearer ${next}`)
})

test('failed refresh clears the session without trying a legacy API alias', async () => {
  fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 }))
    .mockResolvedValueOnce(new Response('{}', { status: 401 }))
  await expect(api.get('/auth/me')).rejects.toMatchObject({ sessionExpired: true })
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/v1/auth/me', '/api/v1/auth/refresh'])
  expect(stored).toBeNull()
})
