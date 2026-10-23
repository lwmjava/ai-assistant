import { useState } from 'react'

import {
  useCreateEvaluationDataset,
  useEvaluationCandidates,
  useEvaluationDatasets,
  useEvaluationDetail,
  useEvaluationReports,
  useEvaluationTrend,
  useEvaluationVersions,
  useReviewEvaluationCandidate,
  useSubmitDatasetVersion,
} from '@/api/evaluations'
import { PageHeader } from '@/components/layout/PageHeader'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { Pager } from '@/components/ui/Pager'
import { formatReportMetric, reportMetrics, reportStatus } from '@/lib/evaluations'
import { can } from '@/lib/permissions'
import { ForbiddenPage } from '@/pages/Forbidden'
import { useAuthStore } from '@/store/auth'
import type { ReportFamily } from '@/types/api'

const familyNames: Record<ReportFamily, string> = { retrieval: 'RAG 检索', generation: 'RAG 生成', agent: 'Agent' }

export default function EvaluationsPage() {
  const role = useAuthStore((s) => s.user?.role)
  const allowed = can(role, 'evaluations', 'read')
  const canWrite = can(role, 'evaluations', 'write')
  const [family, setFamily] = useState<ReportFamily>('retrieval')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const [metric, setMetric] = useState('recall_at_5')
  const [datasetId, setDatasetId] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const [newSlug, setNewSlug] = useState('')
  const reports = useEvaluationReports(family, page, allowed)
  const detail = useEvaluationDetail(selected, allowed)
  const trend = useEvaluationTrend(family, metric, allowed)
  const datasets = useEvaluationDatasets(allowed)
  const versions = useEvaluationVersions(datasetId, allowed)
  const candidates = useEvaluationCandidates(allowed)
  const createDataset = useCreateEvaluationDataset(canWrite)
  const reviewCandidate = useReviewEvaluationCandidate(canWrite)
  const submitVersion = useSubmitDatasetVersion(canWrite)

  if (!allowed) {
    return <ForbiddenPage title="当前角色无权查看评测报告" />
  }

  return <div className="space-y-6">
    <PageHeader title="评测报告" description="查看已登记报告的指标、版本和失败切片。报告中的通过结论不替代发布验收。数据集审核与反馈候选不自动升 Gold。" />
    <div className="flex flex-wrap gap-4">
      <label className="text-sm text-text-muted">报告类型
        <select className="ml-2 rounded border border-border bg-surface p-2 text-text" value={family}
          onChange={(e) => {
            const next = e.target.value as ReportFamily
            setFamily(next); setPage(1); setSelected(null); setMetric(reportMetrics[next][0])
          }}>
          {Object.entries(familyNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
        </select>
      </label>
    </div>
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
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{detail.data.metrics.map((m) => <div key={m.name} className="rounded border border-border p-3">
          <dt className="break-words text-xs text-text-muted">{m.name}</dt><dd className="mt-2 font-mono text-lg text-text">{formatReportMetric(m)}</dd>
        </div>)}</dl>
        <h3 className="font-medium text-text">来源与版本</h3>
        <dl className="grid gap-2 text-sm sm:grid-cols-2">{Object.entries(detail.data.metadata).map(([key, value]) => <div key={key} className="min-w-0">
          <dt className="text-text-muted">{key}</dt><dd className="break-all font-mono text-text">{value ?? '未记录'}</dd>
        </div>)}</dl>
        <h3 className="font-medium text-text">失败切片（{detail.data.failure_count}）</h3>
        {detail.data.failures.length === 0 ? <p className="text-sm text-text-muted">无已记录失败切片；不代表质量门禁通过。</p> :
          <ul className="space-y-2 text-sm text-text-muted">{detail.data.failures.map((f, index) => <li key={`${f.case_id}-${index}`}>
            <span className="font-mono text-text">{f.case_id}</span> · {f.category ?? '类别未记录'} · {f.reason}
          </li>)}</ul>}
        {detail.data.failure_count > detail.data.failures.length && <p className="text-sm text-text-muted">仅展示前 {detail.data.failures.length} 条失败。</p>}
      </div>}
    </section>}
    <section className="panel p-4" aria-label="指标趋势">
      <h2 className="mb-3 font-display text-lg text-text">指标历史</h2>
      <label className="text-sm text-text-muted">指标
        <select value={metric} className="ml-2 max-w-full rounded border border-border bg-surface p-2 text-text" onChange={(e) => setMetric(e.target.value)}>
          {reportMetrics[family].map((name) => <option key={name}>{name}</option>)}
        </select>
      </label>
      <p className="my-3 text-xs text-text-faint">保留报告登记顺序；时间缺失不推断日期。协议与模型口径不同的记录应分别判断，不计算提升幅度。</p>
      {trend.isPending ? <SkeletonRows rows={2} /> : trend.isError ? <ErrorState error={trend.error} onRetry={() => { void trend.refetch() }} /> :
        trend.data.points.length === 0 ? <EmptyState title="暂无指标记录" /> : <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <thead><tr className="text-text-muted"><th className="p-2">报告</th><th className="p-2">数值</th><th className="p-2">协议 / 数据 / 模型</th><th className="p-2">状态</th></tr></thead>
          <tbody>{trend.data.points.map((point) => <tr key={point.report_id} className="border-t border-border text-text">
            <td className="p-2 font-mono">{point.report_id}</td><td className="p-2">{formatReportMetric({ value: point.value, unit: metric === 'cases_failed' ? 'count' : metric === 'latency_p95_ms' ? 'ms' : 'ratio' })}</td>
            <td className="p-2">{point.protocol ?? '未记录'} / {point.metadata.dataset_version ?? '数据版本未记录'} / {point.metadata.model ?? point.metadata.embedding_model ?? '模型未记录'}</td><td className="p-2">{reportStatus[point.status]}</td>
          </tr>)}</tbody>
        </table></div>}
    </section>

    <section className="panel p-4" aria-label="评测数据集">
      <h2 className="mb-3 font-display text-lg text-text">数据集版本</h2>
      <p className="mb-3 text-sm text-text-muted">冻结集与 holdout 不可改成调参数据；AI 生成默认非 Gold。</p>
      {canWrite && <form className="mb-4 flex flex-wrap gap-2" onSubmit={(event) => {
        event.preventDefault()
        if (!newName.trim() || !newSlug.trim()) return
        createDataset.mutate({ name: newName.trim(), slug: newSlug.trim(), family }, {
          onSuccess: (row) => { setDatasetId(row.id); setNewName(''); setNewSlug('') },
        })
      }}>
        <input className="rounded border border-border bg-surface p-2 text-sm" placeholder="名称" value={newName} onChange={(e) => setNewName(e.target.value)} />
        <input className="rounded border border-border bg-surface p-2 text-sm font-mono" placeholder="slug" value={newSlug} onChange={(e) => setNewSlug(e.target.value)} />
        <button type="submit" className="rounded bg-primary px-3 py-2 text-sm text-white disabled:opacity-50" disabled={createDataset.isPending}>创建数据集</button>
      </form>}
      {datasets.isPending ? <SkeletonRows rows={2} /> : datasets.isError ? <ErrorState error={datasets.error} onRetry={() => { void datasets.refetch() }} /> :
        !datasets.data.items.length ? <EmptyState title="暂无数据集" description="导入与审核通过 API 或具备写权限的管理员入口完成。" /> :
        <ul className="mb-4 divide-y divide-border">{datasets.data.items.map((item) => <li key={item.id}>
          <button className="flex w-full items-center justify-between gap-3 p-3 text-left hover:bg-surface-2" aria-pressed={datasetId === item.id} onClick={() => setDatasetId(item.id)}>
            <span className="text-text">{item.name}</span>
            <span className="font-mono text-xs text-text-muted">{item.slug} · {item.family}</span>
          </button>
        </li>)}</ul>}
      {datasetId && (versions.isPending ? <SkeletonRows rows={2} /> : versions.isError ? <ErrorState error={versions.error} onRetry={() => { void versions.refetch() }} /> :
        !versions.data.items.length ? <p className="text-sm text-text-muted">尚无版本；请通过导入接口提交 cases。</p> :
        <ul className="space-y-2 text-sm">{versions.data.items.map((version) => <li key={version.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-border p-3">
          <span className="font-mono text-text">{version.version}</span>
          <span className="text-text-muted">{version.status} · {version.case_count} 条 · {version.is_frozen ? '冻结' : version.source}</span>
          {canWrite && version.status === 'draft' && !version.is_frozen && <button type="button" className="text-primary" onClick={() => submitVersion.mutate({ datasetId, versionId: version.id })}>提交审核</button>}
        </li>)}</ul>)}
    </section>

    <section className="panel p-4" aria-label="反馈回归候选">
      <h2 className="mb-3 font-display text-lg text-text">反馈回归候选</h2>
      <p className="mb-3 text-sm text-text-muted">保留来源与审核记录；接受候选不会自动写入 Gold。</p>
      {candidates.isPending ? <SkeletonRows rows={2} /> : candidates.isError ? <ErrorState error={candidates.error} onRetry={() => { void candidates.refetch() }} /> :
        !candidates.data.items.length ? <EmptyState title="暂无候选" /> :
        <ul className="space-y-2 text-sm">{candidates.data.items.map((item) => <li key={item.id} className="rounded border border-border p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-text">{item.source_type}:{item.source_id}</span>
            <span className="text-text-muted">{item.status}</span>
          </div>
          <p className="mt-2 text-text-muted">来源用户 {item.source_user_id} · 提名 {item.nominated_by}{item.reviewed_by ? ` · 审核 ${item.reviewed_by}` : ''}</p>
          {canWrite && item.status === 'pending' && <div className="mt-2 flex gap-2">
            <button type="button" className="text-primary" onClick={() => reviewCandidate.mutate({ id: item.id, decision: 'accept' })}>接受</button>
            <button type="button" className="text-text-muted" onClick={() => reviewCandidate.mutate({ id: item.id, decision: 'reject' })}>驳回</button>
          </div>}
        </li>)}</ul>}
    </section>
  </div>
}
