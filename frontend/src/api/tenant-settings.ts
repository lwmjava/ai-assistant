/** 租户设置：本租户成员列表与创建。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type { UserInfo } from '@/types/api'

export const tenantMemberKeys = {
  all: ['tenant-members'] as const,
}

export function useTenantMembers(enabled = true) {
  return useQuery({
    queryKey: tenantMemberKeys.all,
    queryFn: () => api.get<UserInfo[]>('/tenant/members'),
    enabled,
  })
}

export function useCreateTenantMember() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { username: string; password: string; email?: string | null }) =>
      api.post<UserInfo>('/tenant/members', body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: tenantMemberKeys.all })
    },
  })
}
