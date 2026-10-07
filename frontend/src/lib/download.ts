/**
 * 受控源文件下载的前端辅助：失败文案映射 + blob 落盘。
 *
 * 后端 `GET /api/rag/documents/{document_id}/download` 会给出 401/403/404/5xx，
 * 这里把它们翻成用户能看懂的中文提示；**映射只改文案，不改权限判定**，
 * 真正的权限校验始终在后端 `can_control_document`。
 */

/** 网络层失败（fetch 直接 reject，拿不到状态码）的占位状态码。 */
export const SOURCE_FILE_NETWORK_FAILURE = 0

function detailOf(payload: unknown): string {
  if (payload && typeof payload === 'object') {
    const row = payload as { detail?: unknown }
    if (typeof row.detail === 'string') return row.detail
  }
  return ''
}

/**
 * 源文件下载失败的中文提示。
 *
 * - 401：会话失效（登录态已过期，需要重新登录）；
 * - 403：已登录但无权下载该文档（不是本人上传，也不是管理员）；
 * - 404：文档不可见或源文件已被清理（例如文本摄取的文档没有源文件）；
 * - 5xx：服务端故障，重试即可；
 * - 0：请求没发出去（网络/代理中断）。
 */
export function sourceFileFailureMessage(status: number, payload: unknown = null): string {
  if (status === SOURCE_FILE_NETWORK_FAILURE) {
    return '网络异常，下载没有完成，请检查网络后重试。'
  }
  if (status === 401) {
    return '登录状态已失效，请重新登录后再下载源文件。'
  }
  if (status === 403) {
    return '无权下载该文档：源文件只能由上传者本人、租户管理员或系统管理员下载。'
  }
  if (status === 404) {
    const detail = detailOf(payload)
    if (detail.includes('源文件')) return `源文件不存在：${detail}`
    return '源文件不存在：该文档可能不是通过文件上传创建的，或源文件已被清理。'
  }
  if (status >= 500) {
    return '服务暂时不可用，下载失败，请稍后重试。'
  }
  return '下载失败，请稍后重试。'
}

/** 把 blob 交给浏览器保存。用完立即释放 object URL，避免内存泄漏。 */
export function saveBlobAsFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
