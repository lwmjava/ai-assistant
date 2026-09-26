/** 系统管理员的用户列表、改角色与停用。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type { Role, UserAdminOut, UserPage } from '@/types/api'

export interface UserQuery {
  page: number
  page_size: number
  username?: string
}

export const userKeys = {
  all: ['admin-users'] as const,
}

export function useUsers(query: UserQuery, enabled = true) {
  const params = new URLSearchParams()
  params.set('page', String(query.page))
  params.set('page_size', String(query.page_size))
  if (query.username) params.set('username', query.username)
  return useQuery({
    queryKey: [...userKeys.all, query],
    queryFn: () => api.get<UserPage>(`/admin/users?${params.toString()}`),
    enabled,
    placeholderData: (prev) => prev,
  })
}

export function useChangeRole() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { userId: string; role: Role }) =>
      api.patch<UserAdminOut>(`/admin/users/${body.userId}`, { role: body.role }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.all })
    },
  })
}

export function useDisableUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (userId: string) => api.post<UserAdminOut>(`/admin/users/${userId}/disable`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.all })
    },
  })
}
