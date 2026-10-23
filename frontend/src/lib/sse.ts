/**
 * SSE 流式解析。
 *
 * 后端 `/api/v1/chat/stream` 是 **POST + SSE**（`sse_starlette.EventSourceResponse`）。
 * 浏览器原生 `EventSource` 只支持 GET 且无法携带请求体，因此这里用 fetch 读取
 * `ReadableStream`，按 SSE 帧规范（`field: value` 行 + 空行分帧）手动解析。
 */

import { API_BASE, notifySessionExpired, refreshTokens } from '@/lib/http'
import { errorCode, errorMessage, terminalIdentity } from '@/lib/api-errors'
import { authSessionEpoch, AuthSessionChangedError } from '@/lib/auth-session'
import { quotaExceededMessage } from '@/lib/quota-message'

export interface SSEMessage {
  event: string
  data: string
  id?: string
}

export interface StreamOptions {
  signal?: AbortSignal
  /** 每解析出一帧即回调；抛出的异常会中断读取。 */
  onMessage: (msg: SSEMessage) => void
  /** 连接异常（网络中断、流被取消）时回调。 */
  onError?: (err: unknown) => void
  onReconnecting?: (reconnecting: boolean) => void
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
    let id: string | undefined
    const dataLines: string[] = []
    for (const rawLine of frame.split(/\r?\n/)) {
      if (!rawLine || rawLine.startsWith(':')) continue // 注释行/心跳
      const idx = rawLine.indexOf(':')
      const field = idx === -1 ? rawLine : rawLine.slice(0, idx)
      let value = idx === -1 ? '' : rawLine.slice(idx + 1)
      if (value.startsWith(' ')) value = value.slice(1)
      if (field === 'event') event = value
      else if (field === 'data') dataLines.push(value)
      else if (field === 'id' && !value.includes('\0')) id = value
    }
    if (dataLines.length) messages.push({ event, data: dataLines.join('\n'), ...(id ? { id } : {}) })
  }
  return { messages, rest: buffer.slice(cursor) }
}

function parseErrorDetail(res: Response, payloadText: string): string {
  if (!res.ok) {
    try {
      const parsed = JSON.parse(payloadText) as { detail?: unknown }
      const quota = quotaExceededMessage(parsed)
      if (quota) return quota
      const normalized = errorMessage(parsed)
      if (normalized) return normalized
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
  lastEventId?: string,
): Promise<Response> {
  return fetch(`${API_BASE}${path}`, {
    method: lastEventId !== undefined ? 'GET' : 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
    },
    ...(lastEventId === undefined ? { body: JSON.stringify(body) } : {}),
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
  const epoch = authSessionEpoch()
  const assertOwner = () => { if (epoch !== authSessionEpoch()) throw new AuthSessionChangedError() }
  const { signal, onMessage, onError, onReconnecting } = options
  const suppliedId = body && typeof body === 'object' && 'run_id' in body ? body.run_id : null
  let runId = typeof suppliedId === 'string' ? suppliedId.replaceAll('-', '') : ''
  let wireRunId = typeof suppliedId === 'string' ? suppliedId : ''
  let lastEventId = ''
  let lastSequence = 0
  let finished = false
  let token = accessToken

  const deliver = (msg: SSEMessage) => {
    assertOwner()
    if (!msg.id) throw new Error('流事件缺少编号')
    if (msg.id) {
      const split = msg.id.lastIndexOf(':')
      const eventRunId = msg.id.slice(0, split)
      const sequence = Number(msg.id.slice(split + 1))
      if (split <= 0 || !/^[a-f\d]{32}$/i.test(eventRunId.replaceAll('-', '')) ||
          !Number.isSafeInteger(sequence) || sequence <= 0 ||
          (runId && eventRunId.replaceAll('-', '') !== runId)) {
        throw new Error('无效的流事件编号')
      }
      let payload: unknown
      try { payload = JSON.parse(msg.data) } catch { throw new Error('无效的流事件内容') }
      if (!payload || typeof payload !== 'object' || !('run_id' in payload) ||
          typeof payload.run_id !== 'string' ||
          payload.run_id.replaceAll('-', '') !== eventRunId.replaceAll('-', '')) {
        throw new Error('流事件运行身份不一致')
      }
      runId = eventRunId.replaceAll('-', '')
      wireRunId = eventRunId
      if (sequence <= lastSequence) return
      if (sequence !== lastSequence + 1) throw new TypeError('event sequence gap')
      lastSequence = sequence
      lastEventId = msg.id
    }
    if (msg.event === 'run.finished') finished = true
    if (msg.event === 'error') {
      let payload: unknown
      try { payload = JSON.parse(msg.data) } catch { /* validated above */ }
      const code = errorCode(payload)
      if (terminalIdentity(code) || code === 'session_expired') {
        const error = Object.assign(new Error(errorMessage(payload)!), { code, status: code === 'session_expired' || code === 'token_revoked' ? 401 : 403 })
        notifySessionExpired(error as Parameters<typeof notifySessionExpired>[0], epoch)
      }
    }
    try {
      onMessage(msg)
    } catch {
      throw new StreamInterruptedError()
    }
  }

  for (let attempt = 0; attempt < 2; attempt++) {
    if (signal?.aborted) return
    const reconnecting = attempt === 1
    if (reconnecting) onReconnecting?.(true)
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
    const requestController = new AbortController()
    const abortRequest = () => requestController.abort()
    signal?.addEventListener('abort', abortRequest, { once: true })
    try {
      const requestPath = reconnecting ? `/runs/${encodeURIComponent(wireRunId)}/events` : path
      const cursor = reconnecting ? lastEventId : undefined
      const response = async () => {
        let timer: ReturnType<typeof setTimeout> | undefined
        return Promise.race([
          openStream(requestPath, body, token, requestController.signal, cursor),
          new Promise<never>((_, reject) => {
            timer = setTimeout(() => {
              requestController.abort()
              reject(new TypeError('response timeout'))
            }, 30_000)
          }),
        ]).finally(() => clearTimeout(timer))
      }
      let res = await response()
      assertOwner()
      if (res.status === 401 || res.status === 403) {
        let payload: unknown
        try { payload = await res.clone().json() } catch { /* legacy response */ }
        const code = errorCode(payload)
        if (terminalIdentity(code)) {
          await discard(res)
          const error = Object.assign(new Error(errorMessage(payload)!), { code, status: res.status })
          notifySessionExpired(error as Parameters<typeof notifySessionExpired>[0], epoch)
          onError?.(error)
          return
        }
      }
      if (res.status === 401) {
        await discard(res)
        const next = await refreshTokens()
        assertOwner()
        if (!next || signal?.aborted) {
          if (!signal?.aborted) {
            const error = notifySessionExpired(undefined, epoch)
            onError?.(error ?? new Error('登录已过期，请重新登录'))
          }
          return
        }
        token = next.access_token
        res = await response()
        assertOwner()
        if (res.status === 401 || res.status === 403) {
          let payload: unknown
          try { payload = await res.clone().json() } catch { /* legacy response */ }
          const code = errorCode(payload)
          if (res.status === 401 || terminalIdentity(code)) {
            const error = Object.assign(new Error(errorMessage(payload) ?? '登录已过期，请重新登录'), { code, status: res.status })
            notifySessionExpired(error as Parameters<typeof notifySessionExpired>[0], epoch)
            onError?.(error)
            await discard(res)
            return
          }
        }
      }
      if (!res.ok || !res.body) {
        const text = await res.text().catch(() => '')
        let payload: unknown
        try { payload = JSON.parse(text) } catch { /* non-JSON response */ }
        const code = errorCode(payload)
        onError?.(reconnecting && !code ? new StreamInterruptedError() : Object.assign(new Error(parseErrorDetail(res, text)), { code, payload }))
        return
      }
      reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      while (!finished && !signal?.aborted) {
        let timer: ReturnType<typeof setTimeout> | undefined
        const next = await Promise.race([
          reader.read(),
          new Promise<never>((_, reject) => {
            timer = setTimeout(() => reject(new TypeError('heartbeat timeout')), 30_000)
          }),
        ]).finally(() => clearTimeout(timer))
        if (next.done) break
        buffer += decoder.decode(next.value, { stream: true })
        const parsed = parseChunk(buffer)
        buffer = parsed.rest
        for (const msg of parsed.messages) deliver(msg)
      }
      if (buffer.trim() && !finished) {
        for (const msg of parseChunk(`${buffer}${decoder.decode()}\n\n`).messages) deliver(msg)
      }
      if (finished || signal?.aborted) return
    } catch (err) {
      if (signal?.aborted) return
      if ((err as Error)?.name === 'AbortError' && requestController.signal.aborted) {
        // 超时中止本次连接，仍可按同一运行编号续流。
      } else
      if (!(err instanceof TypeError)) {
        onError?.(err)
        return
      }
    } finally {
      await reader?.cancel().catch(() => {})
      signal?.removeEventListener('abort', abortRequest)
      requestController.abort()
      if (reconnecting) onReconnecting?.(false)
    }
    if (!wireRunId || attempt === 1) {
      onError?.(new StreamInterruptedError())
      return
    }
  }
}

export class StreamInterruptedError extends Error {
  constructor() {
    super('连接中断，请加载完整回复')
    this.name = 'StreamInterruptedError'
  }
}
