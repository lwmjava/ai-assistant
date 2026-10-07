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

/**
 * 从 `Content-Disposition` 里取后端给出的文件名。
 *
 * 后端 `FileResponse(filename=doc.source or Path(file_path).name)`，所以响应头里的
 * 名字就是后端的权威取值。优先用它，可以把「前端猜测的文件名」这层分歧彻底消掉
 * （前端拿不到磁盘文件基名，只有后端知道）。取不到再按 `source` → `title` 兜底。
 */
export function contentDispositionFilename(header: string | null): string | null {
  if (!header) return null
  // RFC 5987：filename*=UTF-8''%E4%B8%AD%E6%96%87.txt，支持非 ASCII 文件名
  const encoded = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header)
  if (encoded) {
    try {
      const name = decodeURIComponent(encoded[1].trim())
      if (name) return name
    } catch {
      /* 编码异常时退回 filename= */
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header)
  return plain ? plain[1].trim() : null
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
