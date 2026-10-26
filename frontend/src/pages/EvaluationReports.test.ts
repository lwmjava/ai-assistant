import * as React from 'react'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import EvaluationReportsPage from './EvaluationReports'

const state = vi.hoisted(() => ({ role: 'system_admin', empty: false, detailError: false }))
const queries = vi.hoisted(() => ({ detail: vi.fn(), reports: vi.fn(), trend: vi.fn() }))
vi.mock('@/store/auth', () => ({ useAuthStore: (select: (value: unknown) => unknown) => select({ user: { role: state.role } }) }))
vi.mock('@/api/evaluations', () => ({
  useEvaluationJobCases: () => ({ isPending: false, isError: false, data: { items: [], total: 0 } }),
  useEvaluationReports: (family: string, page: number, allowed: boolean) => {
    queries.reports(family, page, allowed)
    return ({
    isPending: false, isError: false,
    data: { total: state.empty ? 0 : 1, items: state.empty ? [] : [{ id: 'retrieval-example', status: 'failed', recorded_at: null }] },
    })
  },
  useEvaluationDetail: (id: string | null, allowed: boolean) => {
    queries.detail(id, allowed)
    return {
      isPending: false, isError: state.detailError, error: new Error('报告不存在或无权访问'), refetch: vi.fn(),
      data: { status: 'passed', protocol: 'real-evaluation-report-1.0.0', metrics: [], metadata: {}, failure_count: 0, failures: [] },
    }
  },
  useEvaluationTrend: (family: string, metric: string, allowed: boolean) => {
    queries.trend(family, metric, allowed)
    return { isPending: false, isError: false, data: { points: [] } }
  },
}))

beforeEach(() => {
  vi.stubGlobal('React', React)
  state.role = 'system_admin'
  state.empty = false
  state.detailError = false
  vi.clearAllMocks()
})

function renderPage(path = '/evaluation-reports') {
  return renderToString(createElement(MemoryRouter, { initialEntries: [path] }, createElement(EvaluationReportsPage)))
}

test('renders report history without run controls', () => {
  const html = renderPage()
  expect(html).toContain('测评报告')
  expect(html).toContain('retrieval-example')
  expect(html).not.toContain('启动测评')
})

test('direct links load the requested report even when absent from the current list, including refresh', () => {
  state.empty = true
  const path = '/evaluation-reports?family=workflow&report_id=off-page-report'
  for (let refresh = 0; refresh < 2; refresh++) {
    const html = renderPage(path)
    expect(html).toContain('aria-label="报告详情"')
    expect(html).toContain('off-page-report')
    expect(queries.detail).toHaveBeenLastCalledWith('off-page-report', true)
    expect(queries.reports).toHaveBeenLastCalledWith('workflow', 1, true)
    expect(queries.trend).toHaveBeenLastCalledWith('workflow', 'completion_rate', true)
  }
})

test('generation links select generation history and its matching default metric', () => {
  renderPage('/evaluation-reports?family=generation&report_id=generation-report')
  expect(queries.reports).toHaveBeenLastCalledWith('generation', 1, true)
  expect(queries.detail).toHaveBeenLastCalledWith('generation-report', true)
  expect(queries.trend).toHaveBeenLastCalledWith('generation', 'answer_point_accuracy', true)
})

test('invalid family does not silently open its report as retrieval', () => {
  const html = renderPage('/evaluation-reports?family=invalid&report_id=untrusted-report')
  expect(queries.detail).toHaveBeenLastCalledWith(null, true)
  expect(html).not.toContain('aria-label="报告详情"')
})

test('unavailable deep-linked report uses the normal error state and unauthorized roles disable its query', () => {
  state.detailError = true
  expect(renderPage('/evaluation-reports?family=agent&report_id=missing')).toContain('报告不存在或无权访问')
  state.role = 'tenant_admin'
  const html = renderPage('/evaluation-reports?family=agent&report_id=missing')
  expect(html).toContain('当前角色无权查看测评报告')
  expect(queries.detail).toHaveBeenLastCalledWith('missing', false)
  expect(html).not.toContain('aria-label="报告详情"')
})

test('renders explicit empty and unauthorized states', () => {
  state.empty = true
  expect(renderPage()).toContain('暂无有权查看的报告')
  state.role = 'tenant_admin'
  expect(renderPage()).toContain('当前角色无权查看测评报告')
})
