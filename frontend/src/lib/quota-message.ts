/** 把配额 429 正文转成给用户看的说明。 */

export function quotaExceededMessage(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null
  const row = payload as { code?: unknown; limit_type?: unknown; used?: unknown; limit?: unknown }
  if (row.code !== 'quota_exceeded') return null
  const used = typeof row.used === 'number' && Number.isFinite(row.used) ? row.used : null
  const limit = typeof row.limit === 'number' && Number.isFinite(row.limit) ? row.limit : null
  if (row.limit_type === 'messages' && used !== null && limit !== null) {
    return `消息条数已达上限：本租户已有 ${used} 条用户消息，上限是 ${limit} 条。请联系管理员提高上限，或删除部分会话后再发送。`
  }
  if (row.limit_type === 'source_bytes' && used !== null && limit !== null) {
    return `源文件容量已达上限：已使用 ${used} 字节，上限是 ${limit} 字节。请联系管理员提高上限。`
  }
  return '已达到配额上限，请联系管理员调整后再试。'
}

export function isQuotaNotice(message: string): boolean {
  return (
    message.startsWith('消息条数已达上限') ||
    message.startsWith('源文件容量已达上限') ||
    message.startsWith('已达到配额上限')
  )
}
