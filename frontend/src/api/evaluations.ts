import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/http'
import { useAuthStore } from '@/store/auth'
import type { EvaluationReportDetail, EvaluationReportPage, EvaluationTrend, ReportFamily } from '@/types/api'

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
