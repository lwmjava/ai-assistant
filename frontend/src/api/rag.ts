/** 知识库（RAG）文档摄取、管理与检索。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type { DocumentOut, SearchResultOut } from '@/types/api'

export const documentKeys = {
  all: ['documents'] as const,
  list: () => [...documentKeys.all, 'list'] as const,
}

export function useDocuments() {
  return useQuery({
    queryKey: documentKeys.list(),
    queryFn: () => api.get<DocumentOut[]>('/rag/documents'),
    staleTime: 15_000,
  })
}

/** 摄取纯文本为知识文档。 */
export function useIngestDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: { text: string; title: string; source?: string }) =>
      api.post<DocumentOut>('/rag/documents/ingest', payload),
    onSuccess: () => void qc.invalidateQueries({ queryKey: documentKeys.all }),
  })
}

/** 上传 .txt / .md 文件（后端仅接受这两种扩展名的 UTF-8 文本）。 */
export function useUploadDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return api.upload<DocumentOut>('/rag/documents/upload', form)
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: documentKeys.all }),
  })
}

export function useDeleteDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.delete<{ deleted: boolean }>(`/rag/documents/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: documentKeys.all }),
  })
}

/** 混合检索（向量 + BM25 + RRF 融合）。查询为手动触发，不自动执行。 */
export function useSearch() {
  return useMutation({
    mutationFn: (payload: { query: string; top_k?: number }) =>
      api.post<SearchResultOut[]>('/rag/search', payload),
  })
}
