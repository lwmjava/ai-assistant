/** HTTP 与流事件共用的错误契约；关联 ID 永远不进入用户文案。 */
export const ERROR_MESSAGES = {
  session_expired: '登录已过期，请重新登录',
  token_revoked: '您的登录凭证已被管理员重置，请重新登录',
  user_disabled: '用户已被禁用，请联系管理员',
  tenant_suspended: '租户已停用，请联系系统管理员',
  rate_limited: '请求过于频繁，请稍后重试',
  quota_exceeded: '本租户配额已用尽，请联系租户管理员',
  model_unavailable: '模型服务暂不可用，请联系管理员检查模型配置',
  file_too_large: '上传文件超过上限',
} as const
export type ErrorCode = keyof typeof ERROR_MESSAGES

export function errorRow(payload: unknown): Record<string, unknown> | null {
  if (!payload || typeof payload !== 'object') return null
  const row = payload as Record<string, unknown>
  return row.error && typeof row.error === 'object' ? row.error as Record<string, unknown> : row
}
export function errorCode(payload: unknown): ErrorCode | undefined {
  const code = errorRow(payload)?.code
  return typeof code === 'string' && Object.prototype.hasOwnProperty.call(ERROR_MESSAGES, code) ? code as ErrorCode : undefined
}
export function errorMessage(payload: unknown): string | null {
  const code = errorCode(payload)
  if (!code) return null
  if (code === 'file_too_large') {
    const root = payload as Record<string, unknown>
    const limit = root.limit_bytes
    if (typeof limit === 'number' && Number.isSafeInteger(limit) && limit > 0) return `${ERROR_MESSAGES[code]}（上限 ${limit} 字节）`
    // 只提取大小数字，不显示任意服务端异常内容。
    const match = String(errorRow(payload)?.message ?? '').match(/上限\s*(\d+)\s*字节/)
    if (match) return `${ERROR_MESSAGES[code]}（上限 ${match[1]} 字节）`
  }
  return ERROR_MESSAGES[code]
}
export function terminalIdentity(code: ErrorCode | undefined): boolean {
  return code === 'token_revoked' || code === 'user_disabled' || code === 'tenant_suspended'
}
