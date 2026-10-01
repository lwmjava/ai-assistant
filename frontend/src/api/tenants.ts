/** 系统管理员的租户列表、创建租户、在租户下创建成员。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type { UserInfo } from '@/types/api'

export interface TenantOut {
  id: string
  name: string
  is_active: boolean
}

export const tenantKeys = {
  all: ['admin-tenants'] as const,
  list: () => [...tenantKeys.all, 'list'] as const,
}

export function useTenants() {
  return useQuery({
    queryKey: tenantKeys.list(),
    queryFn: () => api.get<TenantOut[]>('/admin/tenants'),
  })
}

export function useCreateTenant() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => api.post<TenantOut>('/admin/tenants', { name }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: tenantKeys.all })
    },
  })
}

export function useCreateMember() {
  return useMutation({
    mutationFn: (body: { tenantId: string; username: string; password: string; email?: string }) =>
      api.post<UserInfo>(`/admin/tenants/${body.tenantId}/users`, {
        username: body.username,
        password: body.password,
        email: body.email || undefined,
      }),
  })
}
