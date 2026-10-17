/** 技能列表、详情和写操作。筛选条件放在查询字符串里。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type { SkillDetailOut, SkillListOut, SkillWrite } from '@/types/api'
import type { TenantOut } from '@/api/tenants'

export interface SkillFilters {
  q: string
  scope: '' | 'private' | 'global' | 'builtin'
  enabled: '' | 'true' | 'false'
  tenantId: string
}

export const skillKeys = {
  all: ['skills'] as const,
  list: (filters: SkillFilters) => [...skillKeys.all, 'list', filters] as const,
  detail: (id: string) => [...skillKeys.all, 'detail', id] as const,
}

function listPath(filters: SkillFilters): string {
  const params = new URLSearchParams()
  if (filters.q.trim()) params.set('q', filters.q.trim())
  if (filters.scope) params.set('scope', filters.scope)
  if (filters.enabled) params.set('enabled', filters.enabled)
  if (filters.tenantId && filters.scope !== 'global' && filters.scope !== 'builtin') {
    params.set('tenant_id', filters.tenantId)
  }
  const query = params.toString()
  return query ? `/skills?${query}` : '/skills'
}

export function useSkills(filters: SkillFilters) {
  return useQuery({
    queryKey: skillKeys.list(filters),
    queryFn: () => api.get<SkillListOut[]>(listPath(filters)),
  })
}

export function useSkillDetail(id: string | null) {
  return useQuery({
    queryKey: skillKeys.detail(id ?? ''),
    queryFn: () => api.get<SkillDetailOut>(`/skills/${encodeURIComponent(id ?? '')}`),
    enabled: Boolean(id),
  })
}

export function useSkillTenants(enabled: boolean) {
  return useQuery({
    queryKey: ['admin-tenants', 'skill-filter'],
    queryFn: () => api.get<TenantOut[]>('/admin/tenants'),
    enabled,
  })
}

function useInvalidateSkills() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: skillKeys.all })
}

export function useCreateSkill() {
  const invalidate = useInvalidateSkills()
  return useMutation({
    mutationFn: (body: SkillWrite) => api.post<SkillListOut>('/skills', body),
    onSuccess: invalidate,
  })
}

export function useUpdateSkill() {
  const invalidate = useInvalidateSkills()
  return useMutation({
    mutationFn: (input: { id: string; body: SkillWrite }) =>
      api.patch<SkillListOut>(`/skills/${encodeURIComponent(input.id)}`, input.body),
    onSuccess: invalidate,
  })
}

export function useSetSkillEnabled() {
  const invalidate = useInvalidateSkills()
  return useMutation({
    mutationFn: (input: { id: string; enabled: boolean }) =>
      api.post<SkillListOut>(
        `/skills/${encodeURIComponent(input.id)}/${input.enabled ? 'enable' : 'disable'}`,
      ),
    onSuccess: invalidate,
  })
}

export function useDeleteSkill() {
  const invalidate = useInvalidateSkills()
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/skills/${encodeURIComponent(id)}`),
    onSuccess: invalidate,
  })
}
