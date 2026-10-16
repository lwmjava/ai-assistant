/** 限流等待秒数。大于 0 才显示倒计时；缺失或 null 不启动会自己走完的倒计时。 */

import { ERROR_MESSAGES, errorRow } from './api-errors'

export function retryAfterSeconds(payload: unknown): number | null | undefined {
  if (!payload || typeof payload !== 'object') return undefined
  const row = errorRow(payload)!
  if (row.code !== 'rate_limited') return undefined
  return positiveSeconds('retry_after' in row ? row.retry_after : row.retry_after_seconds)
}

export function retryAfterFromEvent(data: unknown): number | null {
  let row: unknown = data
  if (typeof data === 'string') {
    const text = data.trim()
    if (!text) return null
    try {
      row = JSON.parse(text) as unknown
    } catch {
      return null
    }
  }
  if (!row || typeof row !== 'object') return null
  const parsed = errorRow(row)!
  return positiveSeconds('retry_after' in parsed ? parsed.retry_after : parsed.retry_after_seconds)
}

function positiveSeconds(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= 86400) return Math.ceil(value)
  return null
}

export function isRateLimitNotice(message: string): boolean {
  return message.startsWith('请求过于频繁')
}

export function rateLimitedDetail(payload: unknown): string | null {
  if (retryAfterSeconds(payload) === undefined) return null
  return ERROR_MESSAGES.rate_limited
}
