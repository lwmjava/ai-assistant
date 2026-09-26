/**
 * 流式对话：驱动 SSE 读取与管线阶段状态。
 *
 * 后端 `/chat/stream` 是 POST + SSE，原生 EventSource 不支持，走 `lib/sse.ts` 的
 * fetch + ReadableStream 解析。事件语义：
 * - `stage`：管线阶段推进（理解 / 意图分流 / 规划 / 检索 / 行动 / 质量门自纠错 / 反思 / 响应）
 * - `token`：增量文本
 * - `tool`：工具调用提示
 * - `sources`：检索来源列表，不拼进回复正文
 * - `done`：携带 `state.answer`
 * - `error`：安全拦截或管线异常
 *
 * **关于最终文本取哪个**：后端 `ChatService.chat_stream` 落库用的是
 * `answer = "".join(collected) or state.answer`，即 **token 累积结果**，而不是
 * `done` 事件里的 `state.answer`。实测二者并不相等（`done` 是最终精炼结果，
 * token 累积还包含中间阶段的原始输出）。为保证界面显示与会话历史一致，
 * 这里一律以 token 累积为准，**不要用 done 的 data 覆盖显示文本**。
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { streamPost } from '@/lib/sse'
import type { SourceRef, StreamEventType } from '@/types/api'
import { getAccessToken } from '@/store/auth'

export interface StreamSnapshot {
  streaming: boolean
  /** 已推进的阶段序列（含当前）。 */
  stages: string[]
  currentStage: string | null
  /** 流式累积的助手文本。 */
  text: string
  tools: string[]
  sources: SourceRef[]
  error: string | null
}

const EMPTY: StreamSnapshot = {
  streaming: false,
  stages: [],
  currentStage: null,
  text: '',
  tools: [],
  sources: [],
  error: null,
}

export interface SendOptions {
  message: string
  conversationId: string | null
  /** 后端流式响应不回传 conversation_id，成功后需由调用方刷新列表来定位新会话。 */
  onFinished?: (text: string) => void
}

function asSources(data: unknown): SourceRef[] {
  if (!Array.isArray(data)) return []
  return data.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const row = item as { filename?: unknown; page?: unknown; section?: unknown }
    if (typeof row.filename !== 'string' || !row.filename.trim()) return []
    const page = typeof row.page === 'number' && row.page >= 1 ? row.page : null
    const section = typeof row.section === 'string' && row.section.trim() ? row.section : null
    return [{ filename: row.filename, page, section }]
  })
}

export function useChatStream() {
  const [snapshot, setSnapshot] = useState<StreamSnapshot>(EMPTY)
  const abortRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      abortRef.current?.abort()
    }
  }, [])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setSnapshot((prev) => ({ ...prev, streaming: false }))
  }, [])

  const send = useCallback(
    async ({ message, conversationId, onFinished }: SendOptions) => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      let text = ''
      setSnapshot({ ...EMPTY, streaming: true })

      await streamPost(
        '/chat/stream',
        { message, conversation_id: conversationId },
        getAccessToken(),
        {
          signal: controller.signal,
          onMessage: (msg) => {
            const type = msg.event as StreamEventType
            let payload: { type?: string; data?: unknown } = {}
            try {
              payload = JSON.parse(msg.data) as { type?: string; data?: unknown }
            } catch {
              payload = { data: msg.data }
            }

            if (!mountedRef.current) return

            if (type === 'sources') {
              const sources = asSources(payload.data)
              setSnapshot((prev) => ({ ...prev, sources }))
              return
            }

            const value = typeof payload.data === 'string' ? payload.data : ''

            switch (type) {
              case 'stage':
                setSnapshot((prev) => ({
                  ...prev,
                  currentStage: value,
                  stages: prev.stages.includes(value) ? prev.stages : [...prev.stages, value],
                }))
                break
              case 'token':
                text += value
                setSnapshot((prev) => ({ ...prev, text }))
                break
              case 'tool':
                setSnapshot((prev) => ({ ...prev, tools: [...prev.tools, value] }))
                break
              case 'error':
                setSnapshot((prev) => ({ ...prev, error: value, streaming: false }))
                break
              case 'done':
                // 以 token 累积为准：与后端落库口径一致（见文件头说明）
                onFinished?.(text || value)
                setSnapshot((prev) => ({ ...prev, streaming: false }))
                break
              default:
                break
            }
          },
          onError: (err) => {
            if (!mountedRef.current) return
            setSnapshot((prev) => ({
              ...prev,
              streaming: false,
              error: err instanceof Error ? err.message : '流式连接中断',
            }))
          },
        },
      )

      // 流结束但既未收到 done 也未收到 error：兜底收尾，避免卡在加载态
      setSnapshot((prev) => (prev.streaming ? { ...prev, streaming: false } : prev))
      return text
    },
    [],
  )

  const reset = useCallback(() => setSnapshot(EMPTY), [])

  return { snapshot, send, stop, reset }
}
