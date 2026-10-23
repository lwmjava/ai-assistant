import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import { useAuthStore } from '@/store/auth'
import type {
  EvaluationCandidate,
  EvaluationCandidatePage,
  EvaluationDataset,
  EvaluationDatasetPage,
  EvaluationDatasetVersion,
  EvaluationDatasetVersionPage,
  EvaluationReportDetail,
  EvaluationReportPage,
  EvaluationTrend,
  ReportFamily,
} from '@/types/api'

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
    mutationFn: (body: { name: string; slug: string; family: ReportFamily; description?: string }) =>
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
