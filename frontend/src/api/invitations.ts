/** 邀请码与当前用户的成员关系。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type { InvitationOut, MembershipOut, Token } from '@/types/api'

export const invitationKeys = {
  all: ['invitations'] as const,
  list: (tenantId: string) => [...invitationKeys.all, tenantId] as const,
}

export const membershipKeys = {
  all: ['memberships'] as const,
}

export function useMemberships() {
  return useQuery({
    queryKey: membershipKeys.all,
    queryFn: () => api.get<MembershipOut[]>('/auth/memberships'),
  })
}

export function useInvitations(tenantId: string | null) {
  return useQuery({
    queryKey: invitationKeys.list(tenantId ?? ''),
    queryFn: () => api.get<InvitationOut[]>(`/invitations?tenant_id=${encodeURIComponent(tenantId ?? '')}`),
    enabled: Boolean(tenantId),
  })
}

export function useCreateInvitation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (tenantId: string | null) =>
      api.post<InvitationOut>('/invitations', tenantId ? { tenant_id: tenantId } : {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: invitationKeys.all })
    },
  })
}

export function useAcceptInvitation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (code: string) =>
      api.post<{ tenant_id: string; role: string }>('/invitations/accept', { code }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: membershipKeys.all })
      void qc.invalidateQueries({ queryKey: invitationKeys.all })
    },
  })
}

export function switchTenant(tenantId: string) {
  return api.post<Token>('/auth/switch-tenant', { tenant_id: tenantId })
}
