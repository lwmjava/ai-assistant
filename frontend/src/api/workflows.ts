/** 定时工作流：CRUD、手动触发、启停与执行历史。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type {
  ExecutionFailureAlertOut,
  ExecutionLogDetail,
  ExecutionOut,
  WorkflowAlertOut,
  WorkflowCreate,
  WorkflowDefinition,
  WorkflowOut,
  WorkflowUpdate,
} from '@/types/api'

export interface WorkflowList {
  workflows: WorkflowOut[]
  scheduleMode: string | null
}

export const workflowKeys = {
  all: ['workflows'] as const,
  list: () => [...workflowKeys.all, 'list'] as const,
  executions: (id: string) => [...workflowKeys.all, 'executions', id] as const,
  alerts: () => [...workflowKeys.all, 'alerts'] as const,
  executionAlerts: () => [...workflowKeys.all, 'execution-alerts'] as const,
  executionLogs: (workflowId: string, executionId: string, offset: number) =>
    [...workflowKeys.all, 'execution-logs', workflowId, executionId, offset] as const,
  definition: (id: string) => [...workflowKeys.all, 'definition', id] as const,
}

export function useWorkflows(enabled = true) {
  return useQuery({
    queryKey: workflowKeys.list(),
    queryFn: async (): Promise<WorkflowList> => {
      const response = await api.getResponse('/workflows')
      const text = await response.text()
      const workflows = text ? (JSON.parse(text) as WorkflowOut[]) : []
      return { workflows, scheduleMode: response.headers.get('X-Workflow-Schedule') }
    },
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

/** 创建者失效告警（本租户可见）。 */
export function useWorkflowAlerts(enabled = true) {
  return useQuery({
    queryKey: workflowKeys.alerts(),
    queryFn: () => api.get<WorkflowAlertOut[]>('/workflows/alerts'),
    enabled,
    // 引擎未启用或无权限时不重试，静默隐藏告警条
    retry: false,
  })
}

/** 执行失败告警。与创建者失效告警分开请求。 */
export function useExecutionFailureAlerts(enabled = true) {
  return useQuery({
    queryKey: workflowKeys.executionAlerts(),
    queryFn: () => api.get<ExecutionFailureAlertOut[]>('/workflows/execution-alerts'),
    enabled,
    retry: false,
  })
}

export function useWorkflowDefinition(workflowId: string | null, enabled = true) {
  return useQuery({
    queryKey: workflowKeys.definition(workflowId ?? ''),
    queryFn: () => api.get<WorkflowDefinition>(`/workflows/${workflowId}/definition`),
    enabled: Boolean(workflowId) && enabled,
    retry: false,
  })
}

export function useSaveWorkflowDefinition() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: WorkflowDefinition }) =>
      api.put<WorkflowDefinition>(`/workflows/${id}/definition`, payload),
    onSuccess: async (_data, { id }) => {
      await qc.invalidateQueries({ queryKey: workflowKeys.definition(id) })
    },
  })
}

/** 单次执行的脱敏详情。只在展开时请求，避免把历史查看变成重放。 */
export function useExecutionLogDetail(
  workflowId: string,
  executionId: string,
  offset: number,
  enabled = true,
) {
  return useQuery({
    queryKey: workflowKeys.executionLogs(workflowId, executionId, offset),
    queryFn: () =>
      api.get<ExecutionLogDetail>(
        `/workflows/${workflowId}/executions/${executionId}/logs?offset=${offset}&limit=50`,
      ),
    enabled,
    retry: false,
  })
}
