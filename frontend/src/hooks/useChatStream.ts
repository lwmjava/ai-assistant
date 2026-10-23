/** 规范 SSE 对话状态：运行身份、增量回放、显式取消与终态。 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { errorCode, errorMessage } from '@/lib/api-errors'
import { quotaExceededMessage } from '@/lib/quota-message'
import { api, hideStackTrace } from '@/lib/http'
import { retryAfterFromEvent } from '@/lib/rate-limit'
import { streamPost, StreamInterruptedError } from '@/lib/sse'
import type { SSEMessage } from '@/lib/sse'
import type { ChatAttachmentInput, CodeResult, SourceRef, SubtaskSummary } from '@/types/api'
import { getAccessToken } from '@/store/auth'

export interface StreamSnapshot {
  runId: string | null
  reconnecting: boolean
  cancelling: boolean
  intent: string | null
  shortcut: boolean
  planSteps: string[]
  retryable: boolean
  toolCalls: StreamToolCall[]
  streaming: boolean
  /** 已推进的阶段序列（含当前）。 */
  stages: string[]
  currentStage: string | null
  /** 流式累积的助手文本。 */
  text: string
  tools: string[]
  subtasks: SubtaskSummary[]
  sources: SourceRef[]
  codeResults: CodeResult[]
  error: string | null
  /** 用户点击了停止。已看到的文字留下，不按网络错误处理。 */
  stoppedByUser: boolean
  /** 连接中断，且不是用户主动停止。 */
  interrupted: boolean
}

const EMPTY: StreamSnapshot = {
  runId: null,
  reconnecting: false,
  cancelling: false,
  intent: null,
  shortcut: false,
  planSteps: [],
  retryable: false,
  toolCalls: [],
  streaming: false,
  stages: [],
  currentStage: null,
  text: '',
  tools: [],
  subtasks: [],
  sources: [],
  codeResults: [],
  error: null,
  stoppedByUser: false,
  interrupted: false,
}

export interface SendOptions {
  message: string
  conversationId: string | null
  attachments?: ChatAttachmentInput[]
  /** 收到会话编号。新建会话时用来定位，断线后也靠它拉取。 */
  onConversation?: (conversationId: string) => void
  /** 后端流式响应在 done 前已落库。成功后由调用方刷新详情。 */
  onFinished?: (text: string, status?: 'done' | 'cancelled') => void
  /** 限流事件。大于 0 的秒数才倒计时；null 表示拒绝但不倒计时。 */
  onRateLimit?: (seconds: number | null) => void
}

function asCodeResult(data: unknown): CodeResult | null {
  if (!data || typeof data !== 'object') return null
  const row = data as { status?: unknown; stdout?: unknown; reason?: unknown }
  if (row.status !== 'ok' && row.status !== 'error' && row.status !== 'timeout') return null
  return {
    status: row.status,
    stdout: typeof row.stdout === 'string' ? row.stdout : '',
    reason: typeof row.reason === 'string' ? row.reason : '',
  }
}

function asSubtask(data: unknown): SubtaskSummary | null {
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
  const item = row as { v?: unknown; name?: unknown; status?: unknown; summary?: unknown }
  if (item.v !== 1) return null
  if (item.name !== 'research' && item.name !== 'draft') return null
  if (item.status !== 'done') return null
  if (typeof item.summary !== 'string') return null
  return { name: item.name, status: 'done', summary: item.summary }
}

function asSources(data: unknown): SourceRef[] {
  if (!Array.isArray(data)) return []
  return data.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const row = item as Record<string, unknown>
    const filename = row.filename ?? row.doc
    if (typeof filename !== 'string' || !filename.trim()) return []
    const page = typeof row.page === 'number' && row.page >= 1 ? row.page : null
    const section = typeof row.section === 'string' && row.section.trim() ? row.section : null
    return [{ filename, page, section,
      excerpt: typeof row.excerpt === 'string' ? row.excerpt : null,
      chunk_id: typeof row.chunk_id === 'string' ? row.chunk_id : null,
      document_id: typeof row.document_id === 'string' ? row.document_id : null,
    }]
  })
}

export interface StreamToolCall {
  id: string
  name: string
  startedAt: number
  status: 'running' | 'done' | 'failed' | 'interrupted'
  summary: string
}

function toolLabels(calls: StreamToolCall[], now: number): string[] {
  return calls.map((call) => {
    const seconds = Math.max(0, Math.floor((now - call.startedAt) / 1000))
    const status = call.status === 'running'
      ? `正在执行 ${call.name}${seconds >= 5 ? ` · 已用时 ${seconds}s` : ''}`
      : `${call.name} · ${call.status === 'done' ? '完成' : call.status === 'interrupted' ? '结果未确认' : '失败'}`
    return call.summary ? `${status}：${call.summary}` : status
  })
}

/** 将规范事件应用于同一次运行；未知或其他运行事件不改变当前回复。 */
export function reduceStreamSnapshot(previous: StreamSnapshot, msg: SSEMessage, now = Date.now()): StreamSnapshot {
  let payload: Record<string, unknown>
  try {
    const parsed: unknown = JSON.parse(msg.data)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return previous
    payload = parsed as Record<string, unknown>
  } catch { return previous }
  if (typeof payload.run_id !== 'string') return previous
  if (previous.runId && previous.runId.replaceAll('-', '') !== payload.run_id.replaceAll('-', '')) return previous
  const next = { ...previous, runId: payload.run_id, reconnecting: false }
  switch (msg.event) {
    case 'run.started': return { ...next, streaming: true }
    case 'node.started': {
      if (typeof payload.node !== 'string' || next.shortcut) return next
      const labels: Record<string, string> = { understanding: '理解', preflight: '意图分流', plan: '规划',
        retrieval: '检索', action: '行动', quality_gate: '质量检查', reflection: '反思', respond: '响应' }
      const node = labels[payload.node] ?? payload.node
      return { ...next, currentStage: node,
        stages: next.stages.includes(node) ? next.stages : [...next.stages, node] }
    }
    case 'node.finished': return next
    case 'intent.classified': return { ...next,
      intent: typeof payload.intent === 'string' ? payload.intent : null,
      shortcut: payload.shortcut === true,
      ...(payload.shortcut === true ? { stages: [], currentStage: null } : {}) }
    case 'plan.generated': return { ...next, planSteps: Array.isArray(payload.steps)
      ? payload.steps.flatMap((step) => {
        if (typeof step === 'string') return [step]
        if (!step || typeof step !== 'object') return []
        const item = step as Record<string, unknown>
        const label = item.description ?? item.title ?? item.instruction ?? item.label
        return typeof label === 'string' ? [label] : []
      }) : [] }
    case 'token': return { ...next, text: next.text + (typeof payload.delta === 'string' ? payload.delta : '') }
    case 'citations': return { ...next, sources: asSources(payload.sources) }
    case 'tool.call': {
      if (typeof payload.tool_name !== 'string') return next
      const toolCalls: StreamToolCall[] = [...next.toolCalls, {
        id: typeof payload.call_id === 'string' ? payload.call_id : msg.id ?? `${now}:${next.toolCalls.length}`,
        name: payload.tool_name, startedAt: now, status: 'running',
        summary: typeof payload.args_summary === 'string' ? payload.args_summary : '',
      }]
      return { ...next, toolCalls, tools: toolLabels(toolCalls, now) }
    }
    case 'tool.result': {
      const index = typeof payload.call_id === 'string'
        ? next.toolCalls.findIndex((call) => call.id === payload.call_id)
        : next.toolCalls.reduce((found, call, i) => call.name === payload.tool_name && call.status === 'running' ? i : found, -1)
      if (index < 0) return next
      const toolCalls = next.toolCalls.map((call, i): StreamToolCall => i === index ? { ...call,
        status: payload.ok === true ? 'done' : 'failed',
        summary: typeof payload.result_summary === 'string' ? payload.result_summary : '',
      } : call)
      return { ...next, toolCalls, tools: toolLabels(toolCalls, now) }
    }
    case 'code_result': {
      const item = asCodeResult(payload)
      return item ? { ...next, codeResults: [...next.codeResults, item] } : next
    }
    case 'subtask': {
      const item = asSubtask(payload)
      return item ? { ...next, subtasks: [...next.subtasks, item] } : next
    }
    case 'error': return { ...next, retryable: payload.retryable === true,
      error: quotaExceededMessage(payload) ?? errorMessage(payload) ?? hideStackTrace(typeof payload.message === 'string' ? payload.message : '生成失败，请稍后重试') }
    case 'run.finished': {
      const toolCalls = next.toolCalls.map((call): StreamToolCall => call.status === 'running'
        ? { ...call, status: 'interrupted', summary: '运行已结束，未收到工具结果' } : call)
      return { ...next, streaming: false, currentStage: null, toolCalls, tools: toolLabels(toolCalls, now),
        stoppedByUser: payload.status === 'cancelled',
        ...(payload.status === 'failed' && !next.error ? { error: '生成失败，请稍后重试' } : {}) }
    }
    default: return next
  }
}

export function emptyStreamSnapshot(): StreamSnapshot {
  return { ...EMPTY, stages: [], tools: [], toolCalls: [], planSteps: [], sources: [], codeResults: [], subtasks: [] }
}

export function useChatStream() {
  const [snapshot, setSnapshot] = useState<StreamSnapshot>(emptyStreamSnapshot)
  const abortRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(true)
  const runIdRef = useRef<string | null>(null)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      abortRef.current?.abort()
    }
  }, [])

  const stop = useCallback(async () => {
    setSnapshot((prev) => ({
      ...prev,
      streaming: false,
      cancelling: true,
      stoppedByUser: false,
      interrupted: false,
      error: null,
    }))
    abortRef.current?.abort()
    abortRef.current = null
    const runId = runIdRef.current
    if (!runId) {
      setSnapshot((prev) => ({ ...prev, cancelling: false }))
      return null
    }
    try {
      const result = await api.post<{ status: string; conversation_id: string | null; assistant_message_id?: string | null }>(
        `/runs/${encodeURIComponent(runId)}/cancel`,
      )
      if (mountedRef.current && runIdRef.current === runId) setSnapshot((prev) => {
        const toolCalls = prev.toolCalls.map((call): StreamToolCall => call.status === 'running'
          ? { ...call, status: 'interrupted', summary: '运行已停止，未收到工具结果' } : call)
        return { ...prev, cancelling: false, stoppedByUser: result.status === 'cancelled',
          toolCalls, tools: toolLabels(toolCalls, Date.now()) }
      })
      return result
    } catch {
      if (mountedRef.current && runIdRef.current === runId) setSnapshot((prev) => ({ ...prev,
        cancelling: false, stoppedByUser: false, interrupted: true, error: '停止请求未确认，请加载完整回复查看状态' }))
      return null
    }
  }, [])

  useEffect(() => {
    if (!snapshot.streaming || !snapshot.toolCalls.some((call) => call.status === 'running')) return
    const timer = window.setInterval(() => setSnapshot((prev) => ({ ...prev, tools: toolLabels(prev.toolCalls, Date.now()) })), 1000)
    return () => window.clearInterval(timer)
  }, [snapshot.streaming, snapshot.toolCalls])

  const send = useCallback(
    async ({ message, conversationId, attachments, onConversation, onFinished, onRateLimit }: SendOptions) => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      const runId = crypto.randomUUID()
      runIdRef.current = runId
      let current: StreamSnapshot = { ...emptyStreamSnapshot(), runId, streaming: true }
      let settled = false
      setSnapshot(current)

      await streamPost(
        '/chat/stream',
        { message, conversation_id: conversationId, run_id: runId,
          ...(attachments?.length ? { attachments: attachments.map(({ name, content_base64 }) => ({ name, content_base64 })) } : {}) },
        getAccessToken(),
        {
          signal: controller.signal,
          onReconnecting: (reconnecting) => {
            if (mountedRef.current && !controller.signal.aborted) {
              current = { ...current, reconnecting }
              setSnapshot(current)
            }
          },
          onMessage: (msg) => {
            if (!mountedRef.current || controller.signal.aborted) return
            current = reduceStreamSnapshot(current, msg)
            setSnapshot(current)
            let payload: Record<string, unknown>
            try { payload = JSON.parse(msg.data) as Record<string, unknown> } catch { return }
            if (typeof payload.run_id !== 'string' || payload.run_id.replaceAll('-', '') !== runId.replaceAll('-', '')) return
            if (msg.event === 'conversation' && typeof payload.conversation_id === 'string') {
              onConversation?.(payload.conversation_id)
            }
            if (msg.event === 'error' && errorCode(payload) === 'rate_limited') {
              onRateLimit?.(retryAfterFromEvent(payload))
            }
            if (msg.event === 'run.finished') {
              settled = true
              if (payload.status === 'done' || payload.status === 'cancelled') onFinished?.(current.text, payload.status)
            }
          },
          onError: (err) => {
            if (!mountedRef.current || controller.signal.aborted) return
            const payload = err && typeof err === 'object' && 'payload' in err ? err.payload : null
            if (errorCode(payload) === 'rate_limited') onRateLimit?.(retryAfterFromEvent(payload))
            const message = err instanceof Error ? hideStackTrace(err.message) : '连接中断'
            const interrupted = err instanceof StreamInterruptedError
            current = { ...current, streaming: false, reconnecting: false, interrupted, error: message }
            setSnapshot(current)
          },
        },
      )

      // 流结束但既未收到 done 也未收到 error：结束等待，并给出失败提示。
      setSnapshot((prev) => {
        if (!mountedRef.current || controller.signal.aborted || abortRef.current !== controller || runIdRef.current !== runId) return prev
        if (!prev.streaming || prev.stoppedByUser || settled) return prev
        return { ...prev, streaming: false, error: '生成失败，请稍后重试' }
      })
      return current.text
    },
    [],
  )

  const reset = useCallback(() => {
    abortRef.current?.abort()
    runIdRef.current = null
    setSnapshot(emptyStreamSnapshot())
  }, [])

  return { snapshot, send, stop, reset }
}
