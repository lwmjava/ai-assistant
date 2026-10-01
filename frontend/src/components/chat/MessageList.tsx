/** 消息气泡列表：用户右对齐、助手左对齐，助手回复以 Markdown 渲染。 */

import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Bot, UserRound } from 'lucide-react'

import { StageTracker } from './StageTracker'
import { cn } from '@/lib/cn'
import { formatDateTime } from '@/lib/cn'
import type { CodeResult, MessageOut, SourceRef } from '@/types/api'

export interface MessageListProps {
  messages: MessageOut[]
  /** 流式中的助手文本（未落库，作为临时气泡渲染）。 */
  streamingText: string
  streamingSources?: SourceRef[]
  streamingCodeResults?: CodeResult[]
  stageStages: string[]
  currentStage: string | null
  tools: string[]
  streaming: boolean
  streamError: string | null
  /** 用户主动停止。气泡上标已停止，不显示断线按钮。 */
  stopped?: boolean
  /** 连接中断或已停止等待。展示加载完整回复，不把当前半截说成已完成。 */
  interrupted?: boolean
  interruptTitle?: string
  onLoadReply?: () => void
  loadReplyNote?: string | null
  /**
   * 非流式（POST /chat）等待中：此时后端不回传任何中间态，
   * 没有阶段信息可展示，只渲染等待指示，避免把空的阶段骨架误显示为进度。
   */
  awaiting?: boolean
  className?: string
}

/** 三点跳动指示：无中间态可用时的等待反馈，尊重 prefers-reduced-motion。 */
function TypingDots() {
  return (
    <span
      className="flex items-center gap-1.5 py-1"
      role="status"
      aria-live="polite"
      aria-label="正在等待模型响应"
    >
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1.5 animate-bounce rounded-full bg-text-muted motion-reduce:animate-none"
          style={{ animationDelay: `${i * 150}ms`, animationDuration: '900ms' }}
        />
      ))}
      <span className="ml-1.5 text-xs text-text-faint">正在生成，完成后一次性返回</span>
    </span>
  )
}

function Bubble({
  role,
  children,
  meta,
}: {
  role: string
  children: React.ReactNode
  meta?: string
}) {
  const isUser = role === 'user'
  return (
    <div
      className={cn('flex gap-3', isUser ? 'flex-row-reverse' : 'flex-row')}
    >
      <span
        className={cn(
          'mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg border',
          isUser
            ? 'border-primary/30 bg-primary/15 text-primary'
            : 'border-border bg-surface-2 text-text-muted',
        )}
        aria-hidden
      >
        {isUser ? <UserRound className="size-4" /> : <Bot className="size-4" />}
      </span>

      <div
        className={cn(
          'min-w-0 max-w-[min(46rem,88%)] space-y-1',
          isUser ? 'items-end text-right' : 'items-start',
        )}
      >
        <div
          className={cn(
            'rounded-2xl px-4 py-3 text-left',
            isUser
              ? 'rounded-tr-sm bg-primary/15 text-text ring-1 ring-primary/25'
              : 'rounded-tl-sm border border-border bg-surface/80',
          )}
        >
          {typeof children === 'string' ? (
            <div className={cn('markdown', isUser && 'markdown-user')}>{children}</div>
          ) : (
            children
          )}
        </div>
        {meta && <p className="px-1 text-xs text-text-faint">{meta}</p>}
      </div>
    </div>
  )
}

function sourceLabel(source: SourceRef): string {
  const parts = [source.filename]
  if (source.page != null && source.page >= 1) parts.push(`第 ${source.page} 页`)
  if (source.section) parts.push(source.section)
  return parts.join(' · ')
}

function hideHostPaths(text: string): string {
  return text
    .replace(/\\\\\?[^\s"']+/g, '<sandbox>')
    .replace(/\\\\[^\s"']+/g, '<sandbox>')
    .replace(/(?<![A-Za-z])[A-Za-z]:[\\/][^\s"']*/g, '<sandbox>')
    .replace(/(?<![\w:/])\/(?!\/)[^\s"']*/g, '<sandbox>')
}

function CodeResults({ results }: { results?: CodeResult[] }) {
  if (!results?.length) return null
  return (
    <div className="mt-2 space-y-2">
      {results.map((result, index) => {
        const stdout = hideHostPaths(result.stdout || '')
        const reason = hideHostPaths(result.reason || '')
        const title =
          result.status === 'timeout' ? '执行超时' : result.status === 'error' ? '执行失败' : '标准输出'
        return (
          <div key={`${result.status}-${index}`} className="rounded-lg border border-border bg-surface-2/70 px-3 py-2">
            <p className="text-xs text-text-muted">{title}</p>
            {result.status === 'ok' && !stdout.trim() ? (
              <p className="mt-1 text-sm text-text-faint">没有标准输出</p>
            ) : null}
            {stdout.trim() ? (
              <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-sm text-text">{stdout}</pre>
            ) : null}
            {result.status !== 'ok' && reason ? (
              <p className="mt-1 text-sm text-text">{reason}</p>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

function SourceNotes({ sources }: { sources?: SourceRef[] }) {
  if (!sources?.length) return null
  return (
    <ul className="mt-2 space-y-1 text-xs text-text-faint">
      {sources.map((source, index) => (
        <li key={`${source.filename}-${source.page ?? ''}-${source.section ?? ''}-${index}`}>
          {sourceLabel(source)}
        </li>
      ))}
    </ul>
  )
}

export function MessageList({
  messages,
  streamingText,
  streamingSources = [],
  streamingCodeResults = [],
  stageStages,
  currentStage,
  tools,
  streaming,
  streamError,
  awaiting = false,
  stopped = false,
  interrupted = false,
  interruptTitle = '连接中断',
  onLoadReply,
  loadReplyNote,
  className,
}: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  // 新消息、流式增量或等待态切换时贴底（用户手动上滚时不强制拉回）
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, streamingText, awaiting])

  return (
    <div className={cn('scroll-y space-y-5 px-1 py-4', className)}>
      {messages.map((m) => (
        <Bubble key={m.id} role={m.role} meta={m.model ?? formatDateTime(m.created_at)}>
          {m.role === 'assistant' ? (
            <>
              {m.content ? (
                <div className="markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                </div>
              ) : m.status === 'stopped' ? (
                <p className="text-sm text-text-faint">已停止，没有生成内容</p>
              ) : null}
              {m.content && m.status === 'stopped' && (
                <p className="mt-2 text-xs text-text-faint">已停止</p>
              )}
              <SourceNotes sources={m.sources} />
              <CodeResults results={m.code_results} />
            </>
          ) : (
            <div className="markdown">{m.content}</div>
          )}
        </Bubble>
      ))}

      {(awaiting || streaming || streamingText || streamError || stopped || interrupted) && (
        <Bubble role="assistant">
          {awaiting ? (
            <TypingDots />
          ) : (
            <div className="space-y-3">
              <StageTracker
                stages={stageStages}
                currentStage={currentStage}
                tools={tools}
                streaming={streaming}
              />
              {streamingText ? (
                <>
                  <div className="markdown">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingText}</ReactMarkdown>
                  </div>
                  <SourceNotes sources={streamingSources} />
                  <CodeResults results={streamingCodeResults} />
                  {stopped && <p className="mt-2 text-xs text-text-faint">已停止</p>}
                </>
              ) : stopped ? (
                <>
                  <CodeResults results={streamingCodeResults} />
                  <p className="text-sm text-text-faint">已停止，没有生成内容</p>
                </>
              ) : streaming ? (
                <>
                  <CodeResults results={streamingCodeResults} />
                  <p className="text-sm text-text-faint">正在思考…</p>
                </>
              ) : (
                <CodeResults results={streamingCodeResults} />
              )}
              {interrupted && (
                <div className="space-y-2">
                  <p className="text-sm text-text-muted">{interruptTitle}</p>
                  <button
                    type="button"
                    onClick={onLoadReply}
                    className="min-h-touch rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface-2"
                  >
                    加载完整回复
                  </button>
                  {loadReplyNote && <p className="text-xs text-text-faint">{loadReplyNote}</p>}
                </div>
              )}
              {streamError && !interrupted && (
                <p className="text-sm text-danger">{streamError}</p>
              )}
            </div>
          )}
        </Bubble>
      )}

      <div ref={bottomRef} />
    </div>
  )
}
