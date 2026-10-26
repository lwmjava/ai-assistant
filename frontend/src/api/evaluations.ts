import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import { useAuthStore } from '@/store/auth'
import type {
  EvaluationCandidate,
  EvaluationCandidatePage,
  EvaluationCaseResultPage,
  EvaluationDataset,
  EvaluationDatasetPage,
  EvaluationDatasetVersion,
  EvaluationDatasetVersionPage,
  EvaluationJob,
  EvaluationJobPage,
  EvaluationExecutionUserPage,
  EvaluationReportDetail,
  EvaluationReportPage,
  EvaluationTargetPage,
  EvaluationTargetType,
  EvaluationTrend,
  RetrievalBackend,
  RetrievalComparison,
  RetrievalStrategies,
  ReportFamily,
} from '@/types/api'

export function useEvaluationTargets(
  tenantId: string | null,
  targetType: EvaluationTargetType,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ['evaluation-targets', tenantId, targetType],
    queryFn: () => api.get<EvaluationTargetPage>(
      `/evaluations/targets?tenant_id=${encodeURIComponent(tenantId!)}&target_type=${targetType}`,
    ),
    enabled: enabled && tenantId !== null,
  })
}

export function useEvaluationExecutionUsers(tenantId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ['evaluation-execution-users', tenantId],
    queryFn: () => api.get<EvaluationExecutionUserPage>(
      `/evaluations/execution-users?tenant_id=${encodeURIComponent(tenantId!)}`,
    ),
    enabled: enabled && tenantId !== null,
  })
}

export function useRetrievalStrategies(enabled: boolean) {
  const user = useAuthStore((s) => s.user)
  return useQuery({
    queryKey: ['evaluation-retrieval-strategies', user?.id, user?.tenant_id, user?.role],
    queryFn: () => api.get<RetrievalStrategies>('/evaluations/retrieval/strategies'),
    enabled,
  })
}

export function useCompareRetrieval(enabled: boolean) {
  return useMutation({
    mutationFn: (input: {
      query: string
      baseline: { backend: RetrievalBackend; top_k: number }
      candidate: { backend: RetrievalBackend; top_k: number }
    }) => api.post<RetrievalComparison>('/evaluations/retrieval/compare', input),
    mutationKey: ['evaluation-retrieval-compare', enabled],
  })
}

export function useEvaluationReports(family: ReportFamily, page: number, enabled: boolean) {
  const user = useAuthStore((s) => s.user)
  return useQuery({
    queryKey: ['evaluation-reports', user?.id, user?.tenant_id, user?.role, family, page],
    queryFn: () => api.get<EvaluationReportPage>(`/evaluations/reports?family=${family}&page=${page}&page_size=20`),
    enabled,
  })
}

export function useEvaluationDetail(id: string | null, enabled: boolean) {
  const user = useAuthStore((s) => s.user)
  return useQuery({
    queryKey: ['evaluation-detail', user?.id, user?.tenant_id, user?.role, id],
    queryFn: () => api.get<EvaluationReportDetail>(`/evaluations/reports/${encodeURIComponent(id!)}`),
    enabled: enabled && id !== null,
  })
}

export function useEvaluationJobCases(jobId: string | null, enabled: boolean) {
  const user = useAuthStore((s) => s.user)
  return useQuery({
    queryKey: ['evaluation-job-cases', user?.id, user?.role, jobId],
    queryFn: () => api.get<EvaluationCaseResultPage>(
      `/evaluations/jobs/${encodeURIComponent(jobId!)}/cases?page=1&page_size=200`,
    ),
    enabled: enabled && jobId !== null,
  })
}

export function useEvaluationTrend(family: ReportFamily, metric: string, enabled: boolean) {
  const user = useAuthStore((s) => s.user)
  return useQuery({
    queryKey: ['evaluation-trend', user?.id, user?.tenant_id, user?.role, family, metric],
    queryFn: () => api.get<EvaluationTrend>(`/evaluations/trend?family=${family}&metric=${encodeURIComponent(metric)}`),
    enabled,
  })
}

export function useEvaluationDatasets(enabled: boolean) {
  const user = useAuthStore((s) => s.user)
  return useQuery({
    queryKey: ['evaluation-datasets', user?.id, user?.tenant_id, user?.role],
    queryFn: () => api.get<EvaluationDatasetPage>('/evaluations/datasets?page=1&page_size=50'),
    enabled,
  })
}

export function useEvaluationVersions(datasetId: string | null, enabled: boolean) {
  const user = useAuthStore((s) => s.user)
  return useQuery({
    queryKey: ['evaluation-versions', user?.id, user?.tenant_id, user?.role, datasetId],
    queryFn: () =>
      api.get<EvaluationDatasetVersionPage>(
        `/evaluations/datasets/${encodeURIComponent(datasetId!)}/versions?page=1&page_size=50`,
      ),
    enabled: enabled && datasetId !== null,
  })
}

export function useEvaluationCandidates(enabled: boolean) {
  const user = useAuthStore((s) => s.user)
  return useQuery({
    queryKey: ['evaluation-candidates', user?.id, user?.tenant_id, user?.role],
    queryFn: () => api.get<EvaluationCandidatePage>('/evaluations/candidates?page=1&page_size=50'),
    enabled,
  })
}

export function useCreateEvaluationDataset(enabled: boolean) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; slug: string; family: ReportFamily; tenant_id?: string; description?: string }) =>
      api.post<EvaluationDataset>('/evaluations/datasets', body),
    onSuccess: async () => {
      if (enabled) await client.invalidateQueries({ queryKey: ['evaluation-datasets'] })
    },
  })
}

export function useReviewEvaluationCandidate(enabled: boolean) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { id: string; decision: 'accept' | 'reject' | 'withdraw'; notes?: string }) =>
      api.post<EvaluationCandidate>(`/evaluations/candidates/${encodeURIComponent(input.id)}/review`, {
        decision: input.decision,
        notes: input.notes,
      }),
    onSuccess: async () => {
      if (enabled) await client.invalidateQueries({ queryKey: ['evaluation-candidates'] })
    },
  })
}

export function useSubmitDatasetVersion(enabled: boolean) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { datasetId: string; versionId: string }) =>
      api.post<EvaluationDatasetVersion>(
        `/evaluations/datasets/${encodeURIComponent(input.datasetId)}/versions/${encodeURIComponent(input.versionId)}/submit`,
        {},
      ),
    onSuccess: async () => {
      if (enabled) await client.invalidateQueries({ queryKey: ['evaluation-versions'] })
    },
  })
}

export function useEvaluationJobs(enabled: boolean) {
  const user = useAuthStore((s) => s.user)
  return useQuery({
    queryKey: ['evaluation-jobs', user?.id, user?.tenant_id, user?.role],
    queryFn: () => api.get<EvaluationJobPage>('/evaluations/jobs?page=1&page_size=50'),
    enabled,
    refetchInterval: (query) => query.state.data?.items.some((job) =>
      ['queued', 'running', 'cancel_requested'].includes(job.status)) ? 2000 : false,
  })
}

export function useCreateEvaluationJob(enabled: boolean) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      tenantId: string
      targetType: EvaluationTargetType
      targetIds: string[]
      executionUserId?: string
      datasetId?: string
      versionId?: string
      maxExternalCalls: number
      enableGeneration?: boolean
      enableJudge?: boolean
      maxCostYuan?: number
      dataEgressConfirmed?: boolean
    }) =>
      api.post<EvaluationJob>('/evaluations/jobs', {
        tenant_id: input.tenantId,
        target_type: input.targetType,
        target_ids: input.targetIds,
        execution_user_id: input.executionUserId,
        dataset_id: input.datasetId,
        dataset_version_id: input.versionId,
        enable_generation: input.enableGeneration ?? false,
        enable_judge: input.enableJudge ?? false,
        max_cost_yuan: input.maxCostYuan ?? 0,
        data_egress_confirmed: input.dataEgressConfirmed ?? false,
        idempotency_key: crypto.randomUUID(),
        max_external_calls: input.maxExternalCalls,
      }),
    onSuccess: async () => {
      if (enabled) await client.invalidateQueries({ queryKey: ['evaluation-jobs'] })
    },
  })
}

export function useCancelEvaluationJob(enabled: boolean) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      api.post<EvaluationJob>(`/evaluations/jobs/${encodeURIComponent(id)}/cancel`, {}),
    onSuccess: async () => {
      if (enabled) await client.invalidateQueries({ queryKey: ['evaluation-jobs'] })
    },
  })
}
