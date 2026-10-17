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

export function useTenants(includeInactive = false, enabled = true) {
  return useQuery({
    queryKey: [...tenantKeys.list(), includeInactive],
    queryFn: () =>
      api.get<TenantOut[]>(
        includeInactive ? '/admin/tenants?include_inactive=true' : '/admin/tenants',
      ),
    enabled,
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

export function useRenameTenant() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { tenantId: string; name: string }) =>
      api.patch<TenantOut>(`/admin/tenants/${body.tenantId}`, { name: body.name }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: tenantKeys.all })
    },
  })
}

export function useDeactivateTenant() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (tenantId: string) =>
      api.post<TenantOut>(`/admin/tenants/${tenantId}/deactivate`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: tenantKeys.all })
    },
  })
}

export function useActivateTenant() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (tenantId: string) =>
      api.post<TenantOut>(`/admin/tenants/${tenantId}/activate`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: tenantKeys.all })
    },
  })
}

export interface QuotaOut {
  tenant_id: string
  message_limit: number | null
  storage_limit_bytes: number | null
}

export interface QuotaUpdateBody {
  tenantId: string
  message_limit: number | null
  storage_limit_bytes: number | null
  reason: string
}

export function useTenantQuota(tenantId: string | null) {
  return useQuery({
    queryKey: [...tenantKeys.all, 'quota', tenantId],
    queryFn: () => api.get<QuotaOut>(`/admin/tenants/${tenantId}/quota`),
    enabled: Boolean(tenantId),
  })
}

export function useUpdateTenantQuota() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: QuotaUpdateBody) =>
      api.patch<QuotaOut>(`/admin/tenants/${body.tenantId}/quota`, {
        message_limit: body.message_limit,
        storage_limit_bytes: body.storage_limit_bytes,
        reason: body.reason,
      }),
    onSuccess: (_data, body) => {
      void qc.invalidateQueries({ queryKey: [...tenantKeys.all, 'quota', body.tenantId] })
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
