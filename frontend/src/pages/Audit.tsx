/** 审计日志页：多条件过滤 + 分页（仅 system_admin / system_viewer 可见）。 */

import { useState } from 'react'
import { ChevronLeft, ChevronRight, Filter, ScrollText, ShieldOff } from 'lucide-react'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { Input, Select } from '@/components/ui/Field'
import { useAuditLogs, type AuditQuery } from '@/api/audit'
import { canViewAudit } from '@/lib/permissions'
import { formatDateTime, truncate } from '@/lib/cn'
import { useAuthStore } from '@/store/auth'
import type { AuditLogOut } from '@/types/api'

/** 与后端 `app/audit/models.py` 的 AuditAction 保持一致。 */
const ACTION_OPTIONS: { value: string; label: string }[] = [
  { value: 'user_login', label: '用户登录' },
  { value: 'user_logout', label: '用户登出' },
  { value: 'user_token_refresh', label: '令牌刷新' },
  { value: 'user_create', label: '创建用户' },
  { value: 'user_update', label: '更新用户' },
  { value: 'user_delete', label: '删除用户' },
  { value: 'user_disable', label: '禁用用户' },
  { value: 'user_enable', label: '启用用户' },
  { value: 'user_password_reset', label: '重置密码' },
  { value: 'user_role_change', label: '变更角色' },
  { value: 'tenant_create', label: '创建租户' },
  { value: 'tenant_update', label: '更新租户' },
  { value: 'tenant_delete', label: '删除租户' },
  { value: 'conversation_create', label: '创建会话' },
  { value: 'conversation_delete', label: '删除会话' },
  { value: 'knowledge_base_upload', label: '知识库上传' },
  { value: 'knowledge_base_delete', label: '知识库删除' },
  { value: 'knowledge_base_reindex', label: '知识库重建索引' },
  { value: 'workflow_create', label: '创建工作流' },
  { value: 'workflow_update', label: '更新工作流' },
  { value: 'workflow_delete', label: '删除工作流' },
  { value: 'workflow_execute', label: '执行工作流' },
  { value: 'system_config_update', label: '更新系统配置' },
  { value: 'feature_flag_toggle', label: '切换功能开关' },
  { value: 'cli_dangerous_op', label: 'CLI 危险操作' },
  { value: 'other', label: '其他' },
]

const ACTION_LABEL = new Map(ACTION_OPTIONS.map((o) => [o.value, o.label]))

const RESOURCE_OPTIONS = [
  { value: 'user', label: '用户' },
  { value: 'tenant', label: '租户' },
  { value: 'conversation', label: '会话' },
  { value: 'document', label: '文档' },
  { value: 'workflow', label: '工作流' },
]

/** details 字段存的是 JSON 字符串，这里做安全解析用于展示。 */
function formatDetails(raw: string | null): string {
  if (!raw) return '—'
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>
    return Object.entries(parsed)
      .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
      .join(' · ')
  } catch {
    return raw
  }
}

function LogRow({ log }: { log: AuditLogOut }) {
  return (
    <li className="grid grid-cols-1 gap-2 border-b border-border px-4 py-3 last:border-0 hover:bg-surface-2/40 lg:grid-cols-[9rem_11rem_minmax(0,1fr)_12rem] lg:items-center lg:gap-4">
      <div className="flex items-center gap-2 lg:block">
        <Badge tone="accent">{ACTION_LABEL.get(log.action) ?? log.action}</Badge>
      </div>

      <div className="space-y-0.5 text-xs text-text-faint lg:space-y-1">
        <p className="truncate">
          操作者 <span className="font-mono text-text-muted">{log.user_id ? log.user_id.slice(0, 10) : '系统'}</span>
        </p>
        {log.resource_type && (
          <p className="truncate">
            资源 <span className="text-text-muted">{log.resource_type}</span>
          </p>
        )}
      </div>

      <p className="min-w-0 break-words text-sm text-text-muted" title={log.details ?? undefined}>
        {truncate(formatDetails(log.details), 160)}
      </p>

      <div className="space-y-0.5 text-xs text-text-faint lg:text-right">
        <p>{formatDateTime(log.created_at)}</p>
        {log.ip_address && <p className="font-mono">{log.ip_address}</p>}
      </div>
    </li>
  )
}

export default function AuditPage() {
  const role = useAuthStore((s) => s.user?.role)
  const allowed = canViewAudit(role)

  const [query, setQuery] = useState<AuditQuery>({ page: 1, page_size: 50 })
  const [draft, setDraft] = useState<Omit<AuditQuery, 'page' | 'page_size'>>({})
  const [pageSize, setPageSize] = useState(50)

  const { data, isLoading, isFetching, error, refetch } = useAuditLogs(
    { ...query, page_size: pageSize },
    allowed,
  )

  if (!allowed) {
    return (
      <div className="panel">
        <EmptyState
          icon={<ShieldOff className="size-5" aria-hidden />}
          title="当前角色无权查看审计日志"
          description="审计日志仅对平台管理员与平台审计员开放，权限由后端强制校验。"
        />
      </div>
    )
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  const currentPage = data?.page ?? query.page

  function applyFilters() {
    setQuery({ ...draft, page: 1, page_size: pageSize })
  }

  function resetFilters() {
    setDraft({})
    setQuery({ page: 1, page_size: pageSize })
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="审计日志"
        description="记录登录、令牌刷新、资源变更与工作流执行等安全相关事件，按时间倒序排列。"
        actions={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void refetch()}
            icon={<ScrollText className="size-3.5" aria-hidden />}
          >
            刷新
          </Button>
        }
      />

      <section className="panel p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Select
            label="事件类型"
            value={draft.action ?? ''}
            onChange={(e) => setDraft({ ...draft, action: e.target.value || undefined })}
          >
            <option value="">全部</option>
            {ACTION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>

          <Select
            label="资源类型"
            value={draft.resource_type ?? ''}
            onChange={(e) => setDraft({ ...draft, resource_type: e.target.value || undefined })}
          >
            <option value="">全部</option>
            {RESOURCE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>

          <Input
            label="操作者 ID"
            placeholder="留空则不限"
            value={draft.user_id ?? ''}
            onChange={(e) => setDraft({ ...draft, user_id: e.target.value || undefined })}
          />

          <Input
            label="资源 ID"
            placeholder="留空则不限"
            value={draft.resource_id ?? ''}
            onChange={(e) => setDraft({ ...draft, resource_id: e.target.value || undefined })}
          />

          <Input
            label="起始时间"
            type="datetime-local"
            value={draft.since ?? ''}
            onChange={(e) => setDraft({ ...draft, since: e.target.value || undefined })}
          />

          <Input
            label="结束时间"
            type="datetime-local"
            value={draft.until ?? ''}
            onChange={(e) => setDraft({ ...draft, until: e.target.value || undefined })}
          />

          <Select
            label="每页条数"
            value={String(pageSize)}
            onChange={(e) => {
              const next = Number(e.target.value)
              setPageSize(next)
              setQuery((q) => ({ ...q, page: 1, page_size: next }))
            }}
          >
            {[20, 50, 100, 200].map((n) => (
              <option key={n} value={n}>
                {n} 条
              </option>
            ))}
          </Select>

          <div className="flex items-end gap-2">
            <Button
              variant="primary"
              onClick={applyFilters}
              icon={<Filter className="size-3.5" aria-hidden />}
            >
              应用筛选
            </Button>
            <Button variant="ghost" onClick={resetFilters}>
              重置
            </Button>
          </div>
        </div>
      </section>

      <section className="panel overflow-hidden">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
          <h2 className="font-display text-sm font-semibold text-text">
            事件流
            {isFetching && <span className="ml-2 text-xs font-normal text-text-faint">加载中…</span>}
          </h2>
          <span className="text-xs text-text-faint">
            共 {data?.total ?? 0} 条 · 第 {currentPage} / {totalPages} 页
          </span>
        </header>

        {isLoading ? (
          <SkeletonRows rows={8} className="p-4" />
        ) : error ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : (data?.items.length ?? 0) === 0 ? (
          <EmptyState
            icon={<ScrollText className="size-5" aria-hidden />}
            title="没有符合条件的日志"
            description="调整筛选条件，或重置后查看全部事件。"
            className="py-10"
          />
        ) : (
          <ul>
            {data?.items.map((log) => <LogRow key={log.id} log={log} />)}
          </ul>
        )}

        {data && data.total > 0 && (
          <footer className="flex items-center justify-between gap-3 border-t border-border px-4 py-3">
            <Button
              size="sm"
              variant="ghost"
              disabled={currentPage <= 1}
              onClick={() => setQuery((q) => ({ ...q, page: Math.max(1, currentPage - 1) }))}
              icon={<ChevronLeft className="size-3.5" aria-hidden />}
            >
              上一页
            </Button>
            <span className="text-xs text-text-faint">
              {currentPage} / {totalPages}
            </span>
            <Button
              size="sm"
              variant="ghost"
              disabled={currentPage >= totalPages}
              onClick={() => setQuery((q) => ({ ...q, page: currentPage + 1 }))}
            >
              下一页
              <ChevronRight className="size-3.5" aria-hidden />
            </Button>
          </footer>
        )}
      </section>
    </div>
  )
}
