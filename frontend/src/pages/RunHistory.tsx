import { useState } from 'react'

import { useRunHistory, useRunHistoryDetail } from '@/api/runHistory'
import { PageHeader } from '@/components/layout/PageHeader'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { Pager } from '@/components/ui/Pager'
import { can } from '@/lib/permissions'
import { ForbiddenPage } from '@/pages/Forbidden'
import { useAuthStore } from '@/store/auth'

export default function RunHistoryPage() {
  const role = useAuthStore((s) => s.user?.role)
  const allowed = can(role, 'conversations', 'read')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const history = useRunHistory(page, allowed)
  const detail = useRunHistoryDetail(selected, allowed)

  if (!allowed) return <ForbiddenPage title="当前角色无权查看运行历史" />

  return <div className="space-y-5">
    <PageHeader title="运行历史" description="查看最近 30 天内受权、脱敏的执行阶段、耗时和调用计数；查看历史不会重放模型或工具。" />
    <section className="panel" aria-label="运行历史列表">
      {history.isPending ? <SkeletonRows rows={3} /> : history.isError ? <ErrorState error={history.error} onRetry={() => { void history.refetch() }} /> :
        !history.data.items.length ? <EmptyState title="暂无可见运行记录" /> : <ul className="divide-y divide-border">{history.data.items.map((run) => <li key={run.id}>
          <button type="button" className="grid w-full gap-2 p-4 text-left hover:bg-surface-2 sm:grid-cols-[1fr_auto_auto]" onClick={() => setSelected(run.id)}>
            <span className="font-mono text-sm text-text">{run.id}</span>
            <span className="text-sm text-text-muted">{run.error_code === 'run_cancelled' ? '已取消' : run.error_code === 'run_interrupted' ? '已中断' : run.status} · {run.duration_ms.toFixed(1)} ms</span>
            <span className="text-xs text-text-faint">LLM {run.llm_call_count} · 工具 {run.tool_call_count} · Token {run.input_tokens === null || run.output_tokens === null ? '未知' : run.input_tokens + run.output_tokens}</span>
          </button>
        </li>)}</ul>}
    </section>
    {history.data && <Pager page={page} total={history.data.total} pageCount={Math.max(1, Math.ceil(history.data.total / 20))} onPage={(next) => { setPage(next); setSelected(null) }} />}
    {selected && <section className="panel p-4" aria-label="运行详情">
      <h2 className="mb-3 font-display text-lg text-text">节点时间线</h2>
      {detail.isPending ? <SkeletonRows rows={3} /> : detail.isError ? <ErrorState error={detail.error} onRetry={() => { void detail.refetch() }} /> :
        !detail.data.events.length ? <p className="text-sm text-text-muted">本次运行未启用详细 Trace；摘要仍已持久化。</p> : <ol className="space-y-2">{detail.data.events.map((event, index) => <li key={`${event.offset_ms}-${index}`} className="rounded border border-border p-3 text-sm">
          <span className="text-text">{event.name}</span><span className="ml-2 text-text-muted">{event.type} · +{event.offset_ms.toFixed(1)} ms{event.duration_ms === null ? '' : ` · ${event.duration_ms.toFixed(1)} ms`}</span>
        </li>)}</ol>}
    </section>}
  </div>
}
