/** 审计日志查询（仅 system_admin / system_viewer 可见）。 */

import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type { AuditLogPage } from '@/types/api'

export interface AuditQuery {
  page: number
  page_size: number
  action?: string
  user_id?: string
  tenant_id?: string
  resource_type?: string
  resource_id?: string
  since?: string
  until?: string
}

function buildQueryString(q: AuditQuery): string {
  const params = new URLSearchParams()
  params.set('page', String(q.page))
  params.set('page_size', String(q.page_size))
  if (q.action) params.set('action', q.action)
  if (q.user_id) params.set('user_id', q.user_id)
  if (q.tenant_id) params.set('tenant_id', q.tenant_id)
  if (q.resource_type) params.set('resource_type', q.resource_type)
  if (q.resource_id) params.set('resource_id', q.resource_id)
  if (q.since) params.set('since', q.since)
  if (q.until) params.set('until', q.until)
  return params.toString()
}

export function useAuditLogs(query: AuditQuery, enabled = true) {
  return useQuery({
    queryKey: ['audit-logs', query],
    queryFn: () => api.get<AuditLogPage>(`/admin/audit-logs?${buildQueryString(query)}`),
    enabled,
    // 翻页时保留上一页数据，避免出现整表空白的跳动
    placeholderData: (prev) => prev,
  })
}
