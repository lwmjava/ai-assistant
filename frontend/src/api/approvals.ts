/** 当前用户自己的审批事件。刷新后仍读取同一批服务端记录。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type { ApprovalEvent } from '@/components/approvals/state'

export type { ApprovalEvent }

export interface ApprovalResult {
  id: string
  status: string
  execution_count: number
  result_code: string
}

const approvalKeys = {
  all: ['approvals'] as const,
}

export function useApprovalEvents() {
  return useQuery({
    queryKey: approvalKeys.all,
    queryFn: () => api.get<ApprovalEvent[]>('/approvals/events'),
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  })
}

export function useApproveAction() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      api.post<ApprovalResult>(`/approvals/${encodeURIComponent(id)}/approve`, {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: approvalKeys.all }),
  })
}

export function useRejectAction() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      api.post<ApprovalResult>(`/approvals/${encodeURIComponent(id)}/reject`, {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: approvalKeys.all }),
  })
}

export function useExecuteAction() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      api.post<ApprovalResult>(`/approvals/${encodeURIComponent(id)}/execute`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: approvalKeys.all }),
  })
}
