/** 系统状态：数据库、向量库、版本和启动时间。仅系统管理员可查看。 */

import { ShieldOff } from 'lucide-react'

import { useSystemStatus } from '@/api/system'
import { PageHeader } from '@/components/layout/PageHeader'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { formatDateTime } from '@/lib/cn'
import { canManageTenants } from '@/lib/permissions'
import { useAuthStore } from '@/store/auth'

function label(status: string): string {
  return status === 'ok' ? '连通' : '不通'
}

export default function StatusPage() {
  const role = useAuthStore((s) => s.user?.role)
  const allowed = canManageTenants(role)
  const status = useSystemStatus(allowed)

  if (!allowed) {
    return (
      <div className="panel">
        <EmptyState
          icon={<ShieldOff className="size-5" aria-hidden />}
          title="当前角色无权查看系统状态"
          description="数据库和向量库的连通情况只对平台管理员开放。"
        />
      </div>
    )
  }

  const checks = status.data?.checks

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader title="系统状态" description="查看数据库和当前向量库是否连通，以及本进程的版本和启动时间。" />

      {status.isLoading ? (
        <SkeletonRows rows={3} />
      ) : status.isError ? (
        <ErrorState error={status.error} onRetry={() => void status.refetch()} />
      ) : !checks ? (
        <div className="panel">
          <EmptyState title="没有状态数据" description="服务没有返回数据库或向量库的探测结果。" />
        </div>
      ) : (
        <dl className="panel divide-y divide-border">
          <Row term="整体" value={label(status.data?.status ?? 'error')} />
          <Row term="数据库" value={label(checks.database.status)} />
          <Row term="向量库" value={`${label(checks.vector_store.status)} · ${checks.vector_store.backend}`} />
          <Row term="版本" value={status.data?.version ?? '—'} />
          <Row term="环境" value={status.data?.env ?? '—'} />
          <Row term="启动时间" value={status.data ? formatDateTime(status.data.started_at) : '—'} />
        </dl>
      )}
    </div>
  )
}

function Row({ term, value }: { term: string; value: string }) {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-3 px-4 py-3 text-sm">
      <dt className="text-text-muted">{term}</dt>
      <dd className="text-text">{value}</dd>
    </div>
  )
}
