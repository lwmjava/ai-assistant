import * as React from 'react'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import EvaluationsPage from './Evaluations'

const state = vi.hoisted(() => ({ role: 'system_admin', error: false, empty: false }))
vi.mock('@/store/auth', () => ({ useAuthStore: (select: (s: unknown) => unknown) => select({ user: { role: state.role } }) }))
vi.mock('@/api/evaluations', () => ({
  useEvaluationReports: () => ({
    isPending: false, isError: state.error, error: new Error('报告暂不可用'),
    data: { total: state.empty ? 0 : 1, items: state.empty ? [] : [
      { id: 'retrieval-example', status: 'failed', recorded_at: null },
    ] },
  }),
  useEvaluationDetail: () => ({}),
  useEvaluationTrend: () => ({
    isPending: false, isError: false,
    data: { points: [{ report_id: 'retrieval-example', value: null, status: 'failed', protocol: null, metadata: {} }] },
  }),
  useEvaluationDatasets: () => ({ isPending: false, isError: false, data: { items: [], total: 0 } }),
  useEvaluationVersions: () => ({ isPending: false, isError: false, data: { items: [] } }),
  useEvaluationCandidates: () => ({ isPending: false, isError: false, data: { items: [] } }),
  useCreateEvaluationDataset: () => ({ mutate: vi.fn(), isPending: false }),
  useReviewEvaluationCandidate: () => ({ mutate: vi.fn() }),
  useSubmitDatasetVersion: () => ({ mutate: vi.fn() }),
}))

beforeEach(() => {
  vi.stubGlobal('React', React)
  state.role = 'system_admin'; state.error = false; state.empty = false
})

function renderPage() {
  return renderToString(createElement(MemoryRouter, null, createElement(EvaluationsPage)))
}

test('renders recorded failure, missing metric and absent model explicitly', () => {
  const html = renderPage()
  expect(html).toContain('失败')
  expect(html).toContain('缺测')
  expect(html).toContain('模型未记录')
})

test('unknown ownership is represented by an empty authorized list', () => {
  state.empty = true
  expect(renderPage()).toContain('暂无有权查看的报告')
})

test('load error and unauthorized role never render report entries', () => {
  state.error = true
  expect(renderPage()).not.toContain('aria-label="报告列表"><ul')
  state.role = 'member'
  const html = renderPage()
  expect(html).toContain('403')
  expect(html).toContain('当前角色无权查看评测报告')
  expect(html).toContain('返回首页')
  expect(html).not.toContain('retrieval-example')
})
