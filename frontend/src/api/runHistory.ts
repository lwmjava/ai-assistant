import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/http'
import { useAuthStore } from '@/store/auth'
import type { RunHistoryDetail, RunHistoryPage } from '@/types/api'

export function useRunHistory(page: number, enabled: boolean) {
  const user = useAuthStore((s) => s.user)
  return useQuery({
    queryKey: ['run-history', user?.id, user?.tenant_id, user?.role, page],
    queryFn: () => api.get<RunHistoryPage>(`/run-history?page=${page}&page_size=20`),
    enabled,
  })
}

export function useRunHistoryDetail(id: string | null, enabled: boolean) {
  const user = useAuthStore((s) => s.user)
  return useQuery({
    queryKey: ['run-history-detail', user?.id, user?.tenant_id, user?.role, id],
    queryFn: () => api.get<RunHistoryDetail>(`/run-history/${encodeURIComponent(id!)}`),
    enabled: enabled && id !== null,
  })
}
