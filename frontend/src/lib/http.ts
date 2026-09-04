/** HTTP 客户端：统一注入鉴权头、处理 401 自动刷新、把后端错误归一为 ApiError。 */

/** 归一化后的 API 错误：保留状态码与后端 detail / 字段级校验信息。 */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  readonly payload: unknown

  constructor(status: number, detail: string, payload?: unknown) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.payload = payload
  }

  /** 模块未启用（后端以 503 门控工作流、MCP 等可选能力）。 */
  get isDisabled() {
    return this.status === 503
  }

  /** 权限不足（角色不在此资源的允许列表内）。 */
  get isForbidden() {
    return this.status === 403
  }

  get isUnauthorized() {
    return this.status === 401
  }
}

const BASE = '/api'
const STORAGE_KEY = 'aa-auth'

interface StoredAuth {
  access_token: string
  refresh_token: string
}

/** 令牌读写：auth store 是唯一写入方，http 层只读取，避免循环依赖。 */
function readTokens(): StoredAuth | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { state?: Partial<StoredAuth> } & Partial<StoredAuth>
    const access = parsed.state?.access_token ?? parsed.access_token
    const refresh = parsed.state?.refresh_token ?? parsed.refresh_token
    return access && refresh ? { access_token: access, refresh_token: refresh } : null
  } catch {
    return null
  }
}

function writeTokens(tokens: StoredAuth | null) {
  if (!tokens) {
    localStorage.removeItem(STORAGE_KEY)
    return
  }
  const raw = localStorage.getItem(STORAGE_KEY)
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as { state?: Record<string, unknown> }
      if (parsed.state) {
        parsed.state.access_token = tokens.access_token
        parsed.state.refresh_token = tokens.refresh_token
        localStorage.setItem(STORAGE_KEY, JSON.stringify(parsed))
        return
      }
    } catch {
      /* 结构异常时回退为直接覆盖 */
    }
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ state: tokens, version: 0 }))
}

/** 会话失效回调：由 main.tsx 注入，用于清空 store 并跳转登录。 */
let onSessionExpired: (() => void) | null = null
export function setSessionExpiredHandler(handler: () => void) {
  onSessionExpired = handler
}

/**
 * 令牌刷新成功回调：由 main.tsx 注入，用于把新令牌同步回 auth store。
 *
 * **必须存在**：`writeTokens` 只写 localStorage，而 zustand 的 persist 不会把
 * 外部对 localStorage 的修改反向同步到内存 state。缺少这一步时，任何从 store
 * 读取令牌的调用方（如 SSE 流式请求）会一直拿到登录时的旧令牌，
 * 在 access token 过期后必然 401 且无法自行恢复。
 */
let onTokensRefreshed: ((tokens: StoredAuth) => void) | null = null
export function setTokensRefreshedHandler(handler: (tokens: StoredAuth) => void) {
  onTokensRefreshed = handler
}

/** 并发 401 只触发一次刷新，其余请求共享同一个 Promise。 */
let refreshPromise: Promise<StoredAuth | null> | null = null

async function doRefresh(): Promise<StoredAuth | null> {
  const tokens = readTokens()
  if (!tokens?.refresh_token) return null
  try {
    const res = await fetch(`${BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: tokens.refresh_token }),
    })
    if (!res.ok) return null
    const next = (await res.json()) as StoredAuth
    writeTokens(next)
    // 同步回 auth store，见 setTokensRefreshedHandler 的说明
    onTokensRefreshed?.(next)
    return next
  } catch {
    return null
  }
}

/**
 * 主动刷新访问令牌（供 SSE 等非 api 封装的调用方使用）。
 *
 * 与 `request` 内部的 401 重试共享同一个 Promise，因此并发调用只会刷新一次。
 */
export function refreshTokens(): Promise<StoredAuth | null> {
  return refreshOnce()
}

/**
 * 触发统一的会话失效处理（清空本地令牌 + 通知路由守卫跳转登录）。
 *
 * 供 SSE 等调用方在「刷新令牌也失效」时使用，保证与 `request` 内部的行为一致——
 * 否则用户会卡在当前页面，看不到任何跳转。
 */
export function notifySessionExpired() {
  writeTokens(null)
  onSessionExpired?.()
}

function refreshOnce(): Promise<StoredAuth | null> {
  if (!refreshPromise) {
    refreshPromise = doRefresh().finally(() => {
      refreshPromise = null
    })
  }
  return refreshPromise
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  /** 上传文件时传 FormData，不做 JSON 序列化。 */
  form?: FormData
  /** 跳过鉴权头（登录、刷新等公开端点）。 */
  anonymous?: boolean
  /** 内部标记：重放请求时不再尝试刷新，避免死循环。 */
  _retried?: boolean
  signal?: AbortSignal
}

async function parseError(res: Response): Promise<ApiError> {
  let detail = res.statusText || '请求失败'
  let payload: unknown
  try {
    const data = (await res.json()) as { detail?: unknown }
    payload = data
    if (typeof data?.detail === 'string') detail = data.detail
    else if (Array.isArray(data?.detail)) {
      // Pydantic 字段校验错误：[{loc, msg, type}]
      detail = data.detail
        .map((d: { loc?: unknown[]; msg?: string }) => {
          const loc = Array.isArray(d.loc) ? d.loc.slice(1).join('.') : ''
          return loc ? `${loc}: ${d.msg ?? ''}` : (d.msg ?? '')
        })
        .join('；')
    } else if (data?.detail != null) detail = JSON.stringify(data.detail)
  } catch {
    /* 非 JSON 响应时沿用 statusText */
  }
  return new ApiError(res.status, detail, payload)
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, form, anonymous, _retried, headers, ...rest } = options
  const finalHeaders = new Headers(headers)

  if (!anonymous) {
    const tokens = readTokens()
    if (tokens?.access_token) {
      finalHeaders.set('Authorization', `Bearer ${tokens.access_token}`)
    }
  }
  if (form) {
    // 交给浏览器设置 multipart boundary
  } else if (body !== undefined) {
    if (!finalHeaders.has('Content-Type')) finalHeaders.set('Content-Type', 'application/json')
  }

  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    headers: finalHeaders,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  })

  if (res.status === 401 && !anonymous && !_retried) {
    const next = await refreshOnce()
    if (next) {
      return request<T>(path, { ...options, _retried: true })
    }
    writeTokens(null)
    onSessionExpired?.()
    throw new ApiError(401, '登录状态已失效，请重新登录')
  }

  if (!res.ok) throw await parseError(res)

  if (res.status === 204) return undefined as T
  const text = await res.text()
  if (!text) return undefined as T
  try {
    return JSON.parse(text) as T
  } catch {
    return text as unknown as T
  }
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'POST', body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'PUT', body }),
  delete: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'DELETE' }),
  upload: <T>(path: string, form: FormData, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'POST', form }),
  /** 供 store 直接写入刷新后的令牌。 */
  setTokens: writeTokens,
}

export { readTokens }
