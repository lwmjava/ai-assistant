/** 解析 JWT 过期时间，供请求前主动刷新，避免业务接口先打出 401。 */

/** 提前这么多秒视为即将过期，覆盖时钟偏差与请求排队。 */
export const ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 60

export function readJwtExpirySeconds(token: string): number | null {
  const parts = token.split('.')
  if (parts.length < 2 || !parts[1]) return null
  try {
    const normalized = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const pad =
      normalized.length % 4 === 0 ? '' : '='.repeat(4 - (normalized.length % 4))
    const json = atob(normalized + pad)
    const payload = JSON.parse(json) as { exp?: unknown }
    return typeof payload.exp === 'number' && Number.isFinite(payload.exp)
      ? payload.exp
      : null
  } catch {
    return null
  }
}

export function shouldRefreshAccessToken(
  token: string,
  nowMs: number = Date.now(),
  skewSeconds: number = ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
): boolean {
  const exp = readJwtExpirySeconds(token)
  if (exp == null) return true
  return exp * 1000 <= nowMs + skewSeconds * 1000
}
