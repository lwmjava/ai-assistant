import * as React from 'react'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import EvaluationsPage from './Evaluations'

const state = vi.hoisted(() => ({
  role: 'system_admin', error: false, empty: false,
  jobs: [] as { id: string; runner: string; status: string; report_id: string | null }[],
}))
vi.mock('@/store/auth', () => ({ useAuthStore: (select: (s: unknown) => unknown) => select({ user: { role: state.role } }) }))
vi.mock('@/api/tenants', () => ({ useTenants: () => ({ data: [], isPending: false, isError: false }) }))
vi.mock('@/api/evaluations', () => ({
  useEvaluationTargets: () => ({ isPending: false, isError: false, data: { items: [] } }),
  useEvaluationExecutionUsers: () => ({ isPending: false, isError: false, data: { items: [] } }),
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
  useEvaluationJobs: () => ({ isPending: false, isError: false, data: { items: state.jobs } }),
  useCreateEvaluationJob: () => ({ mutate: vi.fn(), isPending: false }),
  useCancelEvaluationJob: () => ({ mutate: vi.fn(), isPending: false }),
  useRetrievalStrategies: () => ({ isPending: false, isError: false, data: { backends: ['native'] } }),
  useCompareRetrieval: () => ({ mutate: vi.fn(), isPending: false }),
}))

beforeEach(() => {
  vi.stubGlobal('React', React)
  state.role = 'system_admin'; state.error = false; state.empty = false
  state.jobs = []
})

function renderPage() {
  return renderToString(createElement(MemoryRouter, null, createElement(EvaluationsPage)))
}

test('keeps evaluation configuration separate from historical reports', () => {
  const html = renderPage()
  expect(html).toContain('测评目标类型')
  expect(html).toContain('运行状态')
  expect(html).toContain('RAG 快速诊断')
  expect(html).toContain('RAG 评测内容')
  expect(html).toContain('RAG 检索')
  expect(html).toContain('value="generation"')
  expect(html).toContain('最多 10 条用例、30 次模型请求、5 元')
  expect(html).toContain('不评判生成答案质量')
  expect(html).not.toContain('报告列表')
})

test('empty resources do not fabricate evaluation targets', () => {
  state.empty = true
  const html = renderPage()
  expect(html).toContain('暂无人工测试集，可直接使用自动 Silver')
  expect(html).not.toContain('示例 Agent')
})

test('unauthorized role cannot render evaluation controls', () => {
  state.error = true
  state.role = 'member'
  const html = renderPage()
  expect(html).toContain('403')
  expect(html).toContain('当前角色无权使用测评功能')
  expect(html).toContain('返回首页')
  expect(html).not.toContain('运行状态')
})

test('completed jobs link to their own report family and report ID only when available', () => {
  state.jobs = [
    { id: 'completed', runner: 'generation', status: 'succeeded', report_id: 'report:actual' },
    { id: 'unfinished', runner: 'agent', status: 'running', report_id: 'not-ready' },
    { id: 'missing-report', runner: 'workflow', status: 'succeeded', report_id: null },
  ]
  const html = renderPage()
  expect(html).toContain('href="/evaluation-reports?family=generation&amp;report_id=report%3Aactual"')
  expect(html.match(/查看报告/g)).toHaveLength(1)
  expect(html).not.toContain('report_id=not-ready')
})
