import { useState } from 'react'

import {
  useCancelEvaluationJob,
  useCompareRetrieval,
  useCreateEvaluationDataset,
  useCreateEvaluationJob,
  useEvaluationDatasets,
  useEvaluationExecutionUsers,
  useEvaluationJobs,
  useEvaluationTargets,
  useEvaluationVersions,
  useRetrievalStrategies,
  useSubmitDatasetVersion,
} from '@/api/evaluations'
import { useTenants } from '@/api/tenants'
import { PageHeader } from '@/components/layout/PageHeader'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { can } from '@/lib/permissions'
import { ForbiddenPage } from '@/pages/Forbidden'
import { useAuthStore } from '@/store/auth'
import type { EvaluationTargetType, ReportFamily, RetrievalBackend } from '@/types/api'

const targetNames: Record<EvaluationTargetType, string> = {
  rag: 'RAG 文档',
  agent: '系统默认 Agent',
  workflow: 'Workflow',
}

export default function EvaluationsPage() {
  const role = useAuthStore((state) => state.user?.role)
  const allowed = can(role, 'evaluations', 'read')
  const canWrite = can(role, 'evaluations', 'write')
  const [tenantId, setTenantId] = useState<string | null>(null)
  const [targetType, setTargetType] = useState<EvaluationTargetType>('rag')
  const [targetId, setTargetId] = useState<string | null>(null)
  const [executionUserId, setExecutionUserId] = useState<string | null>(null)
  const [maxExternalCalls, setMaxExternalCalls] = useState(0)
  const [enableGeneration, setEnableGeneration] = useState(false)
  const [enableJudge, setEnableJudge] = useState(false)
  const [maxCostYuan, setMaxCostYuan] = useState(0)
  const [dataEgressConfirmed, setDataEgressConfirmed] = useState(false)
  const qualityEnabled = targetType === 'rag' && enableGeneration
  const family: ReportFamily = targetType === 'rag' ? 'retrieval' : targetType
  const [datasetId, setDatasetId] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const [newSlug, setNewSlug] = useState('')
  const [jobVersionId, setJobVersionId] = useState<string | null>(null)
  const [comparisonQuery, setComparisonQuery] = useState('')
  const [baselineBackend, setBaselineBackend] = useState<RetrievalBackend>('native')
  const [candidateBackend, setCandidateBackend] = useState<RetrievalBackend>('native')
  const [baselineTopK, setBaselineTopK] = useState(5)
  const [candidateTopK, setCandidateTopK] = useState(10)
  const tenants = useTenants(false, allowed)
  const targets = useEvaluationTargets(tenantId, targetType, allowed)
  const executionUsers = useEvaluationExecutionUsers(tenantId, allowed && targetType === 'agent')
  const datasets = useEvaluationDatasets(allowed)
  const versions = useEvaluationVersions(datasetId, allowed)
  const createDataset = useCreateEvaluationDataset(canWrite)
  const submitVersion = useSubmitDatasetVersion(canWrite)
  const jobs = useEvaluationJobs(allowed)
  const createJob = useCreateEvaluationJob(canWrite)
  const cancelJob = useCancelEvaluationJob(canWrite)
  const strategies = useRetrievalStrategies(allowed)
  const compareRetrieval = useCompareRetrieval(allowed)
  const visibleDatasets = (datasets.data?.items ?? []).filter((item) =>
    item.tenant_id === tenantId && item.family === family)
  const canStart = Boolean(
    tenantId && targetId && (targetType !== 'agent' || executionUserId)
      && (targetType === 'rag' || (datasetId && jobVersionId))
      && (targetType === 'rag' || maxExternalCalls > 0)
      && (!qualityEnabled || (dataEgressConfirmed && maxExternalCalls >= 1
        && maxExternalCalls <= 30 && maxCostYuan > 0 && maxCostYuan <= 5)),
  )
  const startEvaluation = () => {
    if (!tenantId || !targetId || !canStart) return
    createJob.mutate({
      tenantId, targetType, targetIds: [targetId],
      executionUserId: executionUserId ?? undefined,
      datasetId: jobVersionId ? datasetId ?? undefined : undefined,
      versionId: jobVersionId ?? undefined,
      maxExternalCalls: targetType === 'rag' && !qualityEnabled ? 0 : maxExternalCalls,
      enableGeneration: qualityEnabled,
      enableJudge: qualityEnabled && enableJudge,
      maxCostYuan: qualityEnabled ? maxCostYuan : 0,
      dataEgressConfirmed: qualityEnabled && dataEgressConfirmed,
    })
  }

  if (!allowed) return <ForbiddenPage title="当前角色无权使用测评功能" />

  return <div className="space-y-6">
    <PageHeader title="测评" description="选择系统内真实文档、系统默认 Agent 或 Workflow，配置测试集与预算后启动评测。运行结果在测评报告页查看。" />
    <section className="panel p-4" aria-label="测评目标类型">
      <div className="flex flex-wrap gap-4">
      <label className="text-sm text-text-muted">目标租户
        <select className="ml-2 rounded border border-border bg-surface p-2 text-text" value={tenantId ?? ''}
          onChange={(event) => { setTenantId(event.target.value || null); setTargetId(null); setExecutionUserId(null); setDataEgressConfirmed(false) }}>
          <option value="">请选择租户</option>
          {(tenants.data ?? []).map((tenant) => <option key={tenant.id} value={tenant.id}>{tenant.name}</option>)}
        </select>
      </label>
      <label className="text-sm text-text-muted">评测对象
        <select className="ml-2 rounded border border-border bg-surface p-2 text-text" value={targetType}
          onChange={(event) => { setTargetType(event.target.value as EvaluationTargetType); setTargetId(null); setExecutionUserId(null); setDatasetId(null); setJobVersionId(null); setDataEgressConfirmed(false) }}>
          {Object.entries(targetNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
        </select>
      </label>
      </div>
      {targetType === 'rag' && <div className="mt-3 space-y-2">
        <label className="block text-sm text-text-muted">评测内容
          <select aria-label="RAG 评测内容" className="ml-2 rounded border border-border bg-surface p-2 text-text" value={enableGeneration ? 'generation' : 'retrieval'} onChange={(event) => { setEnableGeneration(event.target.value === 'generation'); setDataEgressConfirmed(false) }}>
            <option value="retrieval">RAG 检索</option>
            <option value="generation">RAG 生成质量</option>
          </select>
        </label>
        <p className="text-sm text-text-muted">检索评测检查相关文档是否被召回及其排名，不评判生成答案质量。</p>
        <p className="text-sm text-text-muted">生成与 Judge 复用当前模型服务；管理员需先配置模型价格并启用。首次验证最多 10 条用例、30 次模型请求、5 元。</p>
        {qualityEnabled && <div className="space-y-3 rounded border border-border p-3">
          <label className="block text-sm text-text-muted"><input type="checkbox" checked={enableJudge} onChange={(event) => setEnableJudge(event.target.checked)} /> 启用 Judge 辅助评分（同模型评分，不能替代业务验收）</label>
          <label className="block text-sm text-text-muted">模型请求上限（含 Embedding）<input type="number" min="1" max="30" className="ml-2 w-24 rounded border border-border bg-surface p-2" value={maxExternalCalls || ''} onChange={(event) => setMaxExternalCalls(Number(event.target.value) || 0)} /></label>
          <label className="block text-sm text-text-muted">费用上限（元）<input type="number" min="0.01" max="5" step="0.01" className="ml-2 w-24 rounded border border-border bg-surface p-2" value={maxCostYuan || ''} onChange={(event) => setMaxCostYuan(Number(event.target.value) || 0)} /></label>
          <label className="block text-sm text-text-muted"><input type="checkbox" checked={dataEgressConfirmed} onChange={(event) => setDataEgressConfirmed(event.target.checked)} /> 确认所选文档和测试集为合成或已脱敏数据，并授权发送到当前配置的模型服务</label>
        </div>}
      </div>}
      {tenantId && (targets.isPending ? <div className="mt-3"><SkeletonRows rows={2} /></div> : targets.isError ? <div className="mt-3"><ErrorState error={targets.error} onRetry={() => { void targets.refetch() }} /></div> :
        <div className="mt-3 space-y-3">
          <label className="block text-sm text-text-muted">真实资源
            <select className="ml-2 max-w-full rounded border border-border bg-surface p-2 text-text" value={targetId ?? ''} onChange={(event) => { setTargetId(event.target.value || null); setDataEgressConfirmed(false) }}>
              <option value="">请选择真实资源</option>
              {targets.data.items.map((target) => <option key={target.id} value={target.id} disabled={target.status !== 'available'}>{target.name}{target.status === 'available' ? '' : '（所有者不可用）'}</option>)}
            </select>
          </label>
          {targets.data.items.length === 0 && <p className="text-sm text-text-muted">该租户没有可评测的真实资源。</p>}
          {targetType === 'agent' && <label className="block text-sm text-text-muted">评测执行主体
            <select className="ml-2 max-w-full rounded border border-border bg-surface p-2 text-text" value={executionUserId ?? ''} onChange={(event) => setExecutionUserId(event.target.value || null)}>
              <option value="">请选择启用中的租户用户</option>
              {(executionUsers.data?.items ?? []).map((user) => <option key={user.id} value={user.id}>{user.username} · {user.role}</option>)}
            </select>
          </label>}
          {targetType === 'agent' && (executionUsers.isPending
            ? <p className="text-sm text-text-muted">正在加载可用执行用户…</p>
            : executionUsers.isError
              ? <ErrorState error={executionUsers.error} onRetry={() => { void executionUsers.refetch() }} />
              : !executionUsers.data?.items.length
                ? <p role="status" className="rounded border border-border bg-surface-2 p-3 text-sm text-text-muted">该租户暂无可用的评测执行用户。请在用户管理中确认该租户有启用且已完成首次改密的租户管理员或成员，再刷新本页。平台管理员账号不自动代替租户执行用户。</p>
                : null)}
          {targetType !== 'rag' && (!datasetId || !jobVersionId) && <p className="text-sm text-text-muted">还需在下方测试集版本中选择一个已审核版本，才能启动测评。</p>}
          {targetType !== 'rag' && <label className="block text-sm text-text-muted">外部调用上限
            <input type="number" min="1" max="1000" className="ml-2 w-28 rounded border border-border bg-surface p-2 text-text" value={maxExternalCalls || ''} onChange={(event) => setMaxExternalCalls(Number(event.target.value) || 0)} />
            <span className="ml-2 text-xs text-text-faint">Agent/Workflow 真实执行必须显式填写，达到上限立即停止。</span>
          </label>}
        </div>)}
      <p className="mt-3 text-sm text-text-muted">本页只负责配置、预算确认、启动和运行状态；历史分析不在此页展示。</p>
      {canWrite && <button type="button" className="mt-4 rounded bg-primary px-4 py-2 text-sm text-white disabled:opacity-50" disabled={createJob.isPending || !canStart} onClick={startEvaluation}>{createJob.isPending ? '正在创建测评…' : '启动真实测评'}</button>}
      {createJob.isError && <div className="mt-3"><ErrorState error={createJob.error} /></div>}
    </section>

    {targetType === 'rag' && <section className="panel p-4" aria-label="检索快速诊断">
      <h2 className="mb-2 font-display text-lg text-text">RAG 快速诊断</h2>
      <p className="mb-3 text-sm text-text-muted">使用当前真实索引比较单次查询，只用于定位候选和耗时，不生成批量质量结论。</p>
      {strategies.isError ? <ErrorState error={strategies.error} onRetry={() => { void strategies.refetch() }} /> :
        <form className="space-y-3" onSubmit={(event) => {
          event.preventDefault()
          if (!comparisonQuery.trim()) return
          compareRetrieval.mutate({
            query: comparisonQuery.trim(),
            baseline: { backend: baselineBackend, top_k: baselineTopK },
            candidate: { backend: candidateBackend, top_k: candidateTopK },
          })
        }}>
          <input className="w-full rounded border border-border bg-surface p-2 text-sm text-text" aria-label="比较查询" placeholder="输入测试查询" value={comparisonQuery} onChange={(event) => setComparisonQuery(event.target.value)} />
          <div className="flex flex-wrap gap-3">
            {([['基线', baselineBackend, setBaselineBackend, baselineTopK, setBaselineTopK], ['候选', candidateBackend, setCandidateBackend, candidateTopK, setCandidateTopK]] as const).map(([label, backend, setBackend, topK, setTopK]) =>
              <fieldset key={label} className="flex items-center gap-2 rounded border border-border p-2 text-sm">
                <legend className="px-1 text-text-muted">{label}</legend>
                <select aria-label={`${label}后端`} className="rounded border border-border bg-surface p-2 text-text" value={backend} onChange={(event) => setBackend(event.target.value as RetrievalBackend)}>
                  {(strategies.data?.backends ?? ['native']).map((name) => <option key={name}>{name}</option>)}
                </select>
                <label className="text-text-muted">Top K <input type="number" min="1" max="50" className="w-20 rounded border border-border bg-surface p-2 text-text" value={topK} onChange={(event) => setTopK(Number(event.target.value))} /></label>
              </fieldset>)}
            <button type="submit" className="rounded bg-primary px-3 py-2 text-sm text-white disabled:opacity-50" disabled={compareRetrieval.isPending || strategies.isPending}>诊断</button>
          </div>
        </form>}
      {compareRetrieval.isError && <div className="mt-3"><ErrorState error={compareRetrieval.error} /></div>}
      {compareRetrieval.data && <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {[compareRetrieval.data.baseline, compareRetrieval.data.candidate].map((run, index) => <div key={index} className="rounded border border-border p-3">
          <h3 className="font-medium text-text">{index === 0 ? '基线' : '候选'} · {run.effective_backend} / top {run.variant.top_k}</h3>
          <p className="my-2 text-xs text-text-faint">{run.elapsed_ms.toFixed(1)} ms</p>
          {!run.candidates.length ? <p className="text-sm text-text-muted">无命中</p> : <ol className="space-y-2 text-sm">{run.candidates.map((hit) => <li key={`${hit.rank}-${hit.chunk_id}`} className="rounded bg-surface-2 p-2">
            <p className="font-mono text-xs text-text-muted">#{hit.rank} · {hit.source ?? hit.document_id} · {hit.score.toFixed(4)}</p>
            <p className="mt-1 text-text">{hit.content_preview}</p>
          </li>)}</ol>}
        </div>)}
      </div>}
    </section>}

    <section className="panel p-4" aria-label="评测数据集">
      <h2 className="mb-3 font-display text-lg text-text">测试集版本</h2>
      <p className="mb-3 text-sm text-text-muted">人工审核数据可晋升 Gold；AI 生成数据只能是 Silver。</p>
      {canWrite && <form className="mb-4 flex flex-wrap gap-2" onSubmit={(event) => {
        event.preventDefault()
        if (!newName.trim() || !newSlug.trim()) return
        if (!tenantId) return
        createDataset.mutate({ name: newName.trim(), slug: newSlug.trim(), family, tenant_id: tenantId }, {
          onSuccess: (row) => { setDatasetId(row.id); setNewName(''); setNewSlug('') },
        })
      }}>
        <input className="rounded border border-border bg-surface p-2 text-sm" placeholder="名称" value={newName} onChange={(event) => setNewName(event.target.value)} />
        <input className="rounded border border-border bg-surface p-2 text-sm font-mono" placeholder="slug" value={newSlug} onChange={(event) => setNewSlug(event.target.value)} />
        <button type="submit" className="rounded bg-primary px-3 py-2 text-sm text-white disabled:opacity-50" disabled={createDataset.isPending}>创建测试集</button>
      </form>}
      {targetType === 'rag' && <p className="mb-3 rounded bg-surface-2 p-3 text-sm text-text-muted">不上传测试数据也可直接运行：系统将从所选真实文档的当前索引分块生成可追溯 Silver 用例。人工审核后才可标记 Gold。</p>}
      {datasets.isPending ? <SkeletonRows rows={2} /> : datasets.isError ? <ErrorState error={datasets.error} onRetry={() => { void datasets.refetch() }} /> :
        !visibleDatasets.length ? <EmptyState title={targetType === 'rag' ? '暂无人工测试集，可直接使用自动 Silver' : '暂无适用测试集'} /> :
        <ul className="mb-4 divide-y divide-border">{visibleDatasets.map((item) => <li key={item.id}>
          <button className="flex w-full items-center justify-between gap-3 p-3 text-left hover:bg-surface-2" aria-pressed={datasetId === item.id} onClick={() => { setDatasetId(item.id); setJobVersionId(null); setDataEgressConfirmed(false) }}>
            <span className="text-text">{item.name}</span><span className="font-mono text-xs text-text-muted">{item.slug} · {item.family}</span>
          </button>
        </li>)}</ul>}
      {datasetId && (versions.isPending ? <SkeletonRows rows={2} /> : versions.isError ? <ErrorState error={versions.error} onRetry={() => { void versions.refetch() }} /> :
        !versions.data.items.length ? <p className="text-sm text-text-muted">尚无版本；请导入真实评测用例。</p> :
        <ul className="space-y-2 text-sm">{versions.data.items.map((version) => <li key={version.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-border p-3">
          <span className="font-mono text-text">{version.version}</span><span className="text-text-muted">{version.status} · {version.case_count} 条</span>
          {canWrite && version.status === 'draft' && !version.is_frozen && <button type="button" className="text-primary" onClick={() => submitVersion.mutate({ datasetId, versionId: version.id })}>提交审核</button>}
          {canWrite && version.status === 'approved' && <button type="button" className="text-primary" onClick={() => { setJobVersionId(version.id); setDataEgressConfirmed(false) }}>选择执行</button>}
        </li>)}</ul>)}
    </section>

    <section className="panel p-4" aria-label="评测任务">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="font-display text-lg text-text">运行状态</h2><p className="text-sm text-text-muted">启动前确认资源快照与预算；当前任务状态会在这里更新。</p></div>
      </div>
      {jobs.isPending ? <SkeletonRows rows={2} /> : jobs.isError ? <ErrorState error={jobs.error} onRetry={() => { void jobs.refetch() }} /> :
        !jobs.data.items.length ? <EmptyState title="暂无评测任务" /> :
        <ul className="space-y-2 text-sm">{jobs.data.items.map((job) => <li key={job.id} className="rounded border border-border p-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-mono text-text">{job.id}</span><span className="text-text-muted">{job.target_type ?? job.runner}/{job.mode} · {job.status} · {job.case_completed}/{job.case_total}</span></div>
          {job.error_code && <p className="mt-2 text-sm text-danger">错误：{job.error_code}</p>}
          {canWrite && ['queued', 'running', 'cancel_requested'].includes(job.status) && <button type="button" className="mt-2 text-primary disabled:opacity-50" disabled={cancelJob.isPending || job.status === 'cancel_requested'} onClick={() => cancelJob.mutate(job.id)}>取消</button>}
        </li>)}</ul>}
    </section>
  </div>
}
