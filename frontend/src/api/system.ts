/** 系统管理员查看数据库和向量库是否连通。 */

import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type { SystemStatus } from '@/types/api'

export function useSystemStatus(enabled = true) {
  return useQuery({
    queryKey: ['admin-system-status'],
    queryFn: () => api.get<SystemStatus>('/admin/system/status'),
    enabled,
  })
}
