import { useState } from 'react'

import {
  useEvaluationDetail,
  useEvaluationJobCases,
  useEvaluationReports,
  useEvaluationTrend,
} from '@/api/evaluations'
import { PageHeader } from '@/components/layout/PageHeader'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { Pager } from '@/components/ui/Pager'
import { formatReportMetric, reportMetrics, reportStatus } from '@/lib/evaluations'
import { can } from '@/lib/permissions'
import { ForbiddenPage } from '@/pages/Forbidden'
import { useAuthStore } from '@/store/auth'
import type { ReportFamily } from '@/types/api'

const familyNames: Record<ReportFamily, string> = {
  retrieval: 'RAG 检索',
  generation: 'RAG 生成',
  agent: 'Agent',
  workflow: 'Workflow',
}

export default function EvaluationReportsPage() {
  const role = useAuthStore((state) => state.user?.role)
  const allowed = can(role, 'evaluations', 'read')
  const [family, setFamily] = useState<ReportFamily>('retrieval')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const [metric, setMetric] = useState('recall_at_5')
  const reports = useEvaluationReports(family, page, allowed)
  const detail = useEvaluationDetail(selected, allowed)
  const jobId = typeof detail.data?.metadata.job_id === 'string' ? detail.data.metadata.job_id : null
  const caseResults = useEvaluationJobCases(jobId, allowed)
  const trend = useEvaluationTrend(family, metric, allowed)

  if (!allowed) return <ForbiddenPage title="当前角色无权查看测评报告" />

  return <div className="space-y-6">
    <PageHeader title="测评报告" description="查看历史结果、统一摘要、类型专属指标和失败明细。报告结论不替代发布验收。" />
    <label className="text-sm text-text-muted">报告类型
      <select className="ml-2 rounded border border-border bg-surface p-2 text-text" value={family}
        onChange={(event) => {
          const next = event.target.value as ReportFamily
          setFamily(next)
          setPage(1)
          setSelected(null)
          setMetric(reportMetrics[next][0])
        }}>
        {Object.entries(familyNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
      </select>
    </label>
    {family === 'generation' && <p className="rounded border border-border bg-surface p-3 text-sm text-text-muted">RAG 生成报告包含引用校验、模型用量和费用。Judge 与生成使用同一模型，分数仅供辅助分析；无参考答案时不提供正确性分数。</p>}
    <section className="panel" aria-label="报告列表">
      {reports.isPending ? <SkeletonRows rows={3} /> : reports.isError ? <ErrorState error={reports.error} onRetry={() => { void reports.refetch() }} /> :
        reports.data.items.length === 0 ? <EmptyState title="暂无有权查看的报告" description="只展示归属明确且已登记的报告。" /> :
        <ul className="divide-y divide-border">{reports.data.items.map((report) => <li key={report.id}>
          <button className="flex w-full flex-wrap items-center justify-between gap-3 p-4 text-left hover:bg-surface-2 focus-visible:outline-primary"
            aria-pressed={selected === report.id} onClick={() => setSelected(report.id)}>
            <span className="font-mono text-sm text-text">{report.id}</span>
            <span className="text-sm text-text-muted">{reportStatus[report.status]}</span>
            <span className="text-xs text-text-faint">{report.recorded_at ?? '时间未记录'}</span>
          </button>
        </li>)}</ul>}
    </section>
    {reports.data && <Pager page={page} total={reports.data.total} pageCount={Math.max(1, Math.ceil(reports.data.total / 20))} onPage={(next) => { setPage(next); setSelected(null) }} />}
    {selected && <section className="panel p-4" aria-label="报告详情">
      <h2 className="mb-4 font-display text-lg text-text">{selected}</h2>
      {detail.isPending ? <SkeletonRows rows={3} /> : detail.isError ? <ErrorState error={detail.error} onRetry={() => { void detail.refetch() }} /> : <div className="space-y-5">
        <p className="text-sm text-text-muted">{reportStatus[detail.data.status]} · {detail.data.protocol ?? '协议未记录'}</p>
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{detail.data.metrics.map((item) => <div key={item.name} className="rounded border border-border p-3">
          <dt className="break-words text-xs text-text-muted">{item.name}</dt><dd className="mt-2 font-mono text-lg text-text">{formatReportMetric(item)}</dd>
        </div>)}</dl>
        <h3 className="font-medium text-text">来源与版本</h3>
        <dl className="grid gap-2 text-sm sm:grid-cols-2">{Object.entries(detail.data.metadata).map(([key, value]) => <div key={key} className="min-w-0">
          <dt className="text-text-muted">{key}</dt><dd className="break-all font-mono text-text">{value ?? '未记录'}</dd>
        </div>)}</dl>
        <h3 className="font-medium text-text">失败切片（{detail.data.failure_count}）</h3>
        {detail.data.failures.length === 0 ? <p className="text-sm text-text-muted">无已记录失败切片；不代表质量门禁通过。</p> :
          <ul className="space-y-2 text-sm text-text-muted">{detail.data.failures.map((failure, index) => <li key={`${failure.case_id}-${index}`}>
            <span className="font-mono text-text">{failure.case_id}</span> · {failure.category ?? '类别未记录'} · {failure.reason}
          </li>)}</ul>}
        {jobId && <div className="space-y-3">
          <h3 className="font-medium text-text">逐用例证据</h3>
          {caseResults.isPending ? <SkeletonRows rows={2} /> : caseResults.isError ? <ErrorState error={caseResults.error} onRetry={() => { void caseResults.refetch() }} /> :
            !caseResults.data.items.length ? <p className="text-sm text-text-muted">暂无逐用例结果。</p> :
            <ul className="space-y-2">{caseResults.data.items.map((item) => <li key={item.id} className="rounded border border-border p-3 text-sm">
              <div className="flex flex-wrap justify-between gap-2"><span className="font-mono text-text">{item.case_id}</span><span className="text-text-muted">{item.status} · {item.elapsed_ms ?? '—'} ms · 外部调用 {item.external_calls_used}</span></div>
              <dl className="mt-2 grid gap-2 sm:grid-cols-2">{item.assertions.map((assertion, index) => <div key={`${assertion.name ?? 'assertion'}-${index}`}><dt className="text-text-muted">{assertion.name ?? 'assertion'}</dt><dd className={assertion.passed ? 'text-success' : 'text-danger'}>{assertion.passed ? '通过' : '失败'}</dd></div>)}</dl>
              <details className="mt-2"><summary className="cursor-pointer text-primary">查看证据</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-surface-2 p-2 text-xs text-text-muted">{JSON.stringify(item.evidence, null, 2)}</pre></details>
            </li>)}</ul>}
        </div>}
      </div>}
    </section>}
    <section className="panel p-4" aria-label="指标趋势">
      <h2 className="mb-3 font-display text-lg text-text">指标历史</h2>
      <label className="text-sm text-text-muted">指标
        <select value={metric} className="ml-2 max-w-full rounded border border-border bg-surface p-2 text-text" onChange={(event) => setMetric(event.target.value)}>
          {reportMetrics[family].map((name) => <option key={name}>{name}</option>)}
        </select>
      </label>
      <p className="my-3 text-xs text-text-faint">协议、测试集和关键配置不同的记录不计算提升幅度。</p>
      {trend.isPending ? <SkeletonRows rows={2} /> : trend.isError ? <ErrorState error={trend.error} onRetry={() => { void trend.refetch() }} /> :
        trend.data.points.length === 0 ? <EmptyState title="暂无指标记录" /> : <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <thead><tr className="text-text-muted"><th className="p-2">报告</th><th className="p-2">数值</th><th className="p-2">协议 / 数据 / 模型</th><th className="p-2">状态</th></tr></thead>
          <tbody>{trend.data.points.map((point) => <tr key={point.report_id} className="border-t border-border text-text">
            <td className="p-2 font-mono">{point.report_id}</td><td className="p-2">{formatReportMetric({ value: point.value, unit: metric === 'cases_failed' ? 'count' : metric === 'latency_p95_ms' ? 'ms' : 'ratio' })}</td>
            <td className="p-2">{point.protocol ?? '未记录'} / {point.metadata.dataset_version ?? '数据版本未记录'} / {point.metadata.model ?? point.metadata.embedding_model ?? '模型未记录'}</td><td className="p-2">{reportStatus[point.status]}</td>
          </tr>)}</tbody>
        </table></div>}
    </section>
  </div>
}
