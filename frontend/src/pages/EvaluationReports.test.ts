import * as React from 'react'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import EvaluationReportsPage from './EvaluationReports'

const state = vi.hoisted(() => ({ role: 'system_admin', empty: false }))
vi.mock('@/store/auth', () => ({ useAuthStore: (select: (value: unknown) => unknown) => select({ user: { role: state.role } }) }))
vi.mock('@/api/evaluations', () => ({
  useEvaluationJobCases: () => ({ isPending: false, isError: false, data: { items: [], total: 0 } }),
  useEvaluationReports: () => ({
    isPending: false, isError: false,
    data: { total: state.empty ? 0 : 1, items: state.empty ? [] : [{ id: 'retrieval-example', status: 'failed', recorded_at: null }] },
  }),
  useEvaluationDetail: () => ({}),
  useEvaluationTrend: () => ({ isPending: false, isError: false, data: { points: [] } }),
}))

beforeEach(() => {
  vi.stubGlobal('React', React)
  state.role = 'system_admin'
  state.empty = false
})

function renderPage() {
  return renderToString(createElement(MemoryRouter, null, createElement(EvaluationReportsPage)))
}

test('renders report history without run controls', () => {
  const html = renderPage()
  expect(html).toContain('测评报告')
  expect(html).toContain('retrieval-example')
  expect(html).not.toContain('启动测评')
})

test('renders explicit empty and unauthorized states', () => {
  state.empty = true
  expect(renderPage()).toContain('暂无有权查看的报告')
  state.role = 'tenant_admin'
  expect(renderPage()).toContain('当前角色无权查看测评报告')
})
