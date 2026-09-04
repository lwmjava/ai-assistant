/**
 * SSE 流式解析。
 *
 * 后端 `/api/chat/stream` 是 **POST + SSE**（`sse_starlette.EventSourceResponse`）。
 * 浏览器原生 `EventSource` 只支持 GET 且无法携带请求体，因此这里用 fetch 读取
 * `ReadableStream`，按 SSE 帧规范（`field: value` 行 + 空行分帧）手动解析。
 */

import { notifySessionExpired, refreshTokens } from '@/lib/http'

export interface SSEMessage {
  event: string
  data: string
}

export interface StreamOptions {
  signal?: AbortSignal
  /** 每解析出一帧即回调；抛出的异常会中断读取。 */
  onMessage: (msg: SSEMessage) => void
  /** 连接异常（网络中断、流被取消）时回调。 */
  onError?: (err: unknown) => void
}

function parseChunk(buffer: string): { messages: SSEMessage[]; rest: string } {
  const messages: SSEMessage[] = []
  // SSE 帧以空行分隔，兼容 \n\n 与 \r\n\r\n
  let cursor = 0
  while (true) {
    const lf = buffer.indexOf('\n\n', cursor)
    const crlf = buffer.indexOf('\r\n\r\n', cursor)
    let end = -1
    let sepLen = 2
    if (lf >= 0 && (crlf < 0 || lf < crlf)) {
      end = lf
      sepLen = 2
    } else if (crlf >= 0) {
      end = crlf
      sepLen = 4
    }
    if (end < 0) break

    const frame = buffer.slice(cursor, end)
    cursor = end + sepLen

    let event = 'message'
    const dataLines: string[] = []
    for (const rawLine of frame.split(/\r?\n/)) {
      if (!rawLine || rawLine.startsWith(':')) continue // 注释行/心跳
      const idx = rawLine.indexOf(':')
      const field = idx === -1 ? rawLine : rawLine.slice(0, idx)
      let value = idx === -1 ? '' : rawLine.slice(idx + 1)
      if (value.startsWith(' ')) value = value.slice(1)
      if (field === 'event') event = value
      else if (field === 'data') dataLines.push(value)
    }
    if (dataLines.length) messages.push({ event, data: dataLines.join('\n') })
  }
  return { messages, rest: buffer.slice(cursor) }
}

function parseErrorDetail(res: Response, payloadText: string): string {
  if (!res.ok) {
    try {
      const parsed = JSON.parse(payloadText) as { detail?: unknown }
      if (typeof parsed.detail === 'string') return parsed.detail
    } catch {
      /* 非 JSON 错误体，回退到状态码文本 */
    }
    return `请求失败（HTTP ${res.status}）`
  }
  return '流读取失败'
}

/**
 * 发起 POST SSE 请求并逐帧回调。
 *
 * 鉴权头无法省略（`require_permission("conversations","write")`），
 * 因此这里同样走 fetch + Bearer，与 http.ts 保持一致。
 */
function openStream(
  path: string,
  body: unknown,
  accessToken: string | null,
  signal?: AbortSignal,
): Promise<Response> {
  return fetch(`/api${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  })
}

/** 丢弃响应体，避免 401/错误响应占着连接不释放。 */
async function discard(res: Response) {
  await res.body?.cancel().catch(() => {})
}

export async function streamPost(
  path: string,
  body: unknown,
  accessToken: string | null,
  options: StreamOptions,
): Promise<void> {
  const { signal, onMessage, onError } = options
  let res: Response
  try {
    res = await openStream(path, body, accessToken, signal)

    // 401：对话页可能已闲置超过 access token 有效期，先刷新再重试一次。
    // 缺少这一步时，用户停留超过 30 分钟后的第一条消息会直接失败。
    if (res.status === 401) {
      await discard(res)
      const next = await refreshTokens()
      if (!next) {
        // 刷新令牌同样失效：走与 http.ts 一致的清理流程，由路由守卫跳登录
        notifySessionExpired()
        onError?.(new Error('登录状态已失效，请重新登录'))
        return
      }
      res = await openStream(path, body, next.access_token, signal)
    }
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') return
    onError?.(err)
    return
  }

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '')
    onError?.(new Error(parseErrorDetail(res, text)))
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const { messages, rest } = parseChunk(buffer)
      buffer = rest
      for (const msg of messages) onMessage(msg)
    }
    // 流结束时可能有未以空行收尾的最后一帧
    if (buffer.trim()) {
      const { messages } = parseChunk(`${buffer}\n\n`)
      for (const msg of messages) onMessage(msg)
    }
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') return
    onError?.(err)
  } finally {
    // `onMessage` 抛异常或读取出错时，必须显式关闭 reader，
    // 否则底层 HTTP 连接不会被释放（SSE 是长连接，泄漏代价更高）。
    // 流已正常结束时 cancel() 是 no-op。
    await reader.cancel().catch(() => {})
  }
}
