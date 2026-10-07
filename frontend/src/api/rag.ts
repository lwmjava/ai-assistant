/** 知识库（RAG）文档摄取、管理与检索。 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, isSessionExpiredError } from '@/lib/http'
import {
  saveBlobAsFile,
  sourceFileFailureMessage,
  contentDispositionFilename,
  SOURCE_FILE_NETWORK_FAILURE,
} from '@/lib/download'
import type { ChunkEvidenceOut, DocumentChunkOut, DocumentOut, ImportJobOut, SearchResultOut } from '@/types/api'
export const documentKeys = {
  all: ['documents'] as const,
  list: (includeDeleted = false, versionState = '') =>
    [...documentKeys.all, 'list', includeDeleted, versionState] as const,
}
export function useDocuments(includeDeleted = false, versionState = '') {
  const params = new URLSearchParams()
  if (includeDeleted) params.set('include_deleted', 'true')
  if (versionState) params.set('version_state', versionState)
  const query = params.toString()
  return useQuery({
    queryKey: documentKeys.list(includeDeleted, versionState),
    queryFn: () => api.get<DocumentOut[]>(`/rag/documents${query ? `?${query}` : ''}`),
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
/** 上传知识文档，后端会按文件类型自动解析文本。 */
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
    mutationFn: (input: { id: string; confirmationId?: string }) => {
      const query = input.confirmationId ? `?confirmation_id=${input.confirmationId}` : ''
      return api.delete<{ deleted: boolean }>(`/rag/documents/${input.id}${query}`)
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: documentKeys.all }),
  })
}
export function useReparseDocument() {
  return useMutation({
    mutationFn: (input: { id: string; confirmationId?: string }) => {
      const query = input.confirmationId ? `?confirmation_id=${input.confirmationId}` : ''
      return api.post<ImportJobOut>(`/rag/documents/${input.id}/reparse${query}`)
    },
  })
}
export function fetchImportJob(id: string) {
  return api.get<ImportJobOut>(`/rag/import-jobs/${id}`)
}
export function usePublishDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: { id: string; confirmationId?: string }) => {
      const query = input.confirmationId ? `?confirmation_id=${input.confirmationId}` : ''
      return api.post<DocumentOut>(`/rag/documents/${input.id}/publish${query}`)
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: documentKeys.all }),
  })
}
export async function createConfirmation(documentId: string, action: string) {
  const row = await api.post<{ id: string }>('/rag/operation-confirmations', {
    document_id: documentId,
    action,
  })
  return row.id
}
/** 混合检索（向量 + BM25 + RRF 融合）。查询为手动触发，不自动执行。 */
export function useSearch() {
  return useMutation({
    mutationFn: (payload: { query: string; top_k?: number }) =>
      api.post<SearchResultOut[]>('/rag/search', payload),
  })
}

/**
 * 下载文档源文件（走带凭据的 blob 请求，不用 `window.open` 直链，否则丢鉴权头）。
 *
 * 文件名优先级：后端 `Content-Disposition` 里的权威取值（服务端是
 * `doc.source or Path(file_path).name`）→ `doc.source` → 文档标题。
 * 这样前端不会另造一个与后端不一致的名字。
 *
 * 失败一律抛 Error（会话失效则原样抛 ApiError，交给上层跳过提示并跳登录）。
 */
export async function downloadDocumentSource(doc: DocumentOut): Promise<void> {
  let payload: { blob: Blob; headers: Headers }
  try {
    payload = await api.blob(`/rag/documents/${doc.id}/download`)
  } catch (err) {
    if (isSessionExpiredError(err)) throw err
    if (err instanceof ApiError) {
      throw new ApiError(err.status, sourceFileFailureMessage(err.status, err.payload), err.payload)
    }
    throw new Error(sourceFileFailureMessage(SOURCE_FILE_NETWORK_FAILURE))
  }
  const filename =
    contentDispositionFilename(payload.headers.get('Content-Disposition')) ??
    doc.source ??
    doc.title
  saveBlobAsFile(payload.blob, filename)
}

/** 文档分块列表（逐块详情）。 */
export function useDocumentChunks(documentId: string) {
  return useQuery({
    queryKey: ['document-chunks', documentId] as const,
    queryFn: () => api.get<DocumentChunkOut[]>(`/rag/documents/${documentId}/chunks`),
    enabled: Boolean(documentId),
    staleTime: 15_000,
  })
}

/**
 * 引用原文核验（ADR-0007 只读核验）。
 *
 * 只取回命中块正文与定位信息；权限由后端每次请求独立复核，前端不预判。
 * `chunkId` 传 null 表示尚未展开，不发请求。
 */
export function useChunkEvidence(chunkId: string | null) {
  return useQuery({
    queryKey: ['chunk-evidence', chunkId] as const,
    queryFn: () => api.get<ChunkEvidenceOut>(`/rag/chunks/${chunkId}/evidence`),
    enabled: Boolean(chunkId),
    staleTime: 60_000,
    // 无权 / 已失效是终态结论，重试只会放大噪声。
    retry: false,
  })
}
