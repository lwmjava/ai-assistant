/** 定时工作流：CRUD、手动触发、启停与执行历史。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type { ExecutionOut, WorkflowCreate, WorkflowOut, WorkflowUpdate } from '@/types/api'

export const workflowKeys = {
  all: ['workflows'] as const,
  list: () => [...workflowKeys.all, 'list'] as const,
  executions: (id: string) => [...workflowKeys.all, 'executions', id] as const,
}

export function useWorkflows(enabled = true) {
  return useQuery({
    queryKey: workflowKeys.list(),
    queryFn: () => api.get<WorkflowOut[]>('/workflows'),
    enabled,
    // 引擎未启用时后端整组返回 503，不重试，直接交给页面展示
    retry: false,
  })
}

export function useWorkflowExecutions(workflowId: string | null, enabled = true) {
  return useQuery({
    queryKey: workflowKeys.executions(workflowId ?? ''),
    queryFn: () => api.get<ExecutionOut[]>(`/workflows/${workflowId}/executions`),
    enabled: Boolean(workflowId) && enabled,
    retry: false,
  })
}

export function useCreateWorkflow() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: WorkflowCreate) => api.post<WorkflowOut>('/workflows', payload),
    onSuccess: () => void qc.invalidateQueries({ queryKey: workflowKeys.all }),
  })
}

export function useUpdateWorkflow() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: WorkflowUpdate }) =>
      api.put<WorkflowOut>(`/workflows/${id}`, payload),
    onSuccess: () => void qc.invalidateQueries({ queryKey: workflowKeys.all }),
  })
}

export function useDeleteWorkflow() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.delete<{ deleted: boolean; id: string }>(`/workflows/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: workflowKeys.all }),
  })
}

/** 手动触发一次执行（以任务 owner 身份运行）。 */
export function useRunWorkflow() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.post<ExecutionOut>(`/workflows/${id}/run`, {}),
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: workflowKeys.executions(id) })
      void qc.invalidateQueries({ queryKey: workflowKeys.all })
    },
  })
}

export function useToggleWorkflow() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.post<WorkflowOut>(`/workflows/${id}/toggle`, { enabled }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: workflowKeys.all }),
  })
}
