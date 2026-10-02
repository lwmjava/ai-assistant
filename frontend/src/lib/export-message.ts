/** 导出失败时给页面的说明。不包含对话正文。 */

export function exportFailureMessage(status: number, payload: unknown): string {
  const row =
    payload && typeof payload === 'object'
      ? (payload as { code?: unknown; detail?: unknown })
      : null
  const code = row?.code
  const detail = typeof row?.detail === 'string' ? row.detail : ''
  if (status === 403 || detail === '无权导出') {
    return '无权导出。只有当前租户的租户管理员可以下载本租户对话。'
  }
  if (status === 413 || code === 'export_too_large') {
    return '导出内容过大，没有开始下载。'
  }
  if (status === 503 || code === 'export_audit_failed') {
    return '审计没有写入，没有开始下载。'
  }
  return '导出失败，请稍后重试。'
}
