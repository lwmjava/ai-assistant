/** 把配额 429 正文转成给用户看的说明。 */

import { errorCode, ERROR_MESSAGES } from './api-errors'

export function quotaExceededMessage(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null
  const row = payload as {
    code?: unknown
    limit_type?: unknown
    used?: unknown
    limit?: unknown
    resets_at?: unknown
  }
  // 用量在响应根上；嵌套 error 只提供稳定错误码，不能据此丢掉上限和重置时间。
  if (row.code !== 'quota_exceeded' && errorCode(payload) !== 'quota_exceeded') return null
  const used = typeof row.used === 'number' && Number.isFinite(row.used) ? row.used : null
  const limit = typeof row.limit === 'number' && Number.isFinite(row.limit) ? row.limit : null
  const reset =
    typeof row.resets_at === 'string' && row.resets_at.trim() ? `下次重置时间：${row.resets_at}。` : ''
  if (row.limit_type === 'messages' && used !== null && limit !== null) {
    return `消息条数已达上限：本租户已有 ${used} 条用户消息，上限是 ${limit} 条。${reset}请联系管理员提高上限，或删除部分会话后再发送。`
  }
  if (row.limit_type === 'source_bytes' && used !== null && limit !== null) {
    return `源文件容量已达上限：已使用 ${used} 字节，上限是 ${limit} 字节。${reset}请联系管理员提高上限。`
  }
  if (row.limit_type === 'tokens' && used !== null && limit !== null) {
    return `Token 已达上限：本月已用 ${used}，上限是 ${limit}。${reset}请联系管理员提高上限，或等待自然月重置。`
  }
  return null
}

export function isQuotaNotice(message: string): boolean {
  return (
    message.startsWith('消息条数已达上限') ||
    message.startsWith(ERROR_MESSAGES.quota_exceeded) ||
    message.startsWith('源文件容量已达上限') ||
    message.startsWith('Token 已达上限') ||
    message.startsWith('已达到配额上限')
  )
}
