/** 系统状态：健康、在线用户、存储与本进程自然日调用量。仅系统管理员可查看。 */

import { useSystemStatus } from '@/api/system'
import { PageHeader } from '@/components/layout/PageHeader'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { formatDateTime } from '@/lib/cn'
import { canManageTenants } from '@/lib/permissions'
import { ForbiddenPage } from '@/pages/Forbidden'
import { useAuthStore } from '@/store/auth'

function label(status: string): string {
  return status === 'ok' ? '连通' : '不通'
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

export default function StatusPage() {
  const role = useAuthStore((s) => s.user?.role)
  const allowed = canManageTenants(role)
  const status = useSystemStatus(allowed)

  if (!allowed) {
    return (
      <ForbiddenPage
        title="当前角色无权查看系统状态"
        description="健康与运行指标只对平台管理员开放。"
      />
    )
  }

  const checks = status.data?.checks
  const data = status.data

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title="系统状态"
        description="同一页查看服务健康、在线用户、知识库存储用量，以及本进程今日 API 调用量。"
      />

      {status.isLoading ? (
        <SkeletonRows rows={3} />
      ) : status.isError ? (
        <ErrorState error={status.error} onRetry={() => void status.refetch()} />
      ) : !checks || !data ? (
        <div className="panel">
          <EmptyState title="没有状态数据" description="服务没有返回数据库或向量库的探测结果。" />
        </div>
      ) : (
        <dl className="panel divide-y divide-border">
          <Row term="整体" value={label(data.status)} />
          <Row term="数据库" value={label(checks.database.status)} />
          <Row term="向量库" value={`${label(checks.vector_store.status)} · ${checks.vector_store.backend}`} />
          <Row term="在线用户" value={`${data.online_users}（过去 15 分钟）`} />
          <Row term="存储用量" value={formatBytes(data.storage_bytes)} />
          <Row term="API 调用量" value={`${data.api_request_count}（今日本进程）`} />
          <Row term="版本" value={data.version ?? '—'} />
          <Row term="环境" value={data.env ?? '—'} />
          <Row term="启动时间" value={formatDateTime(data.started_at)} />
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
