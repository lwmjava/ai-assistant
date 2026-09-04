/** 输入区：自适应高度、Enter 发送 / Shift+Enter 换行、流式/一次性模式切换。 */

import { useEffect, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { ArrowUp, MessageSquare, Square, Waves } from 'lucide-react'

import { cn } from '@/lib/cn'
import type { SendMode } from '@/types/api'

export interface ComposerProps {
  value: string
  onChange: (next: string) => void
  onSubmit: () => void
  onStop?: () => void
  /** 流式进行中：显示「停止」而非「发送」，且不可切换模式。 */
  streaming: boolean
  /** 任意模式正在等待响应：禁用发送与模式切换，避免并发写入同一会话。 */
  busy?: boolean
  /** 当前发送模式。 */
  mode: SendMode
  onModeChange: (mode: SendMode) => void
  disabled?: boolean
  placeholder?: string
}

const MODE_META: Record<SendMode, { label: string; hint: string }> = {
  stream: {
    label: '流式',
    hint: '边生成边返回，可实时看到管线阶段与工具调用；可随时中断',
  },
  once: {
    label: '一次性',
    hint: '等待完整结果后一次返回，会带回会话 ID；等待期间看不到中间过程',
  },
}

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  streaming,
  busy = false,
  mode,
  onModeChange,
  disabled = false,
  placeholder = '输入消息，Enter 发送，Shift+Enter 换行',
}: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null)

  // 高度自适应：上限 200px，超出后内部滚动
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [value])

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // 输入法组合期间不拦截 Enter，避免打断中文/日文选词
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      if (value.trim() && !streaming && !busy && !disabled) onSubmit()
    }
  }

  const canSend = Boolean(value.trim()) && !streaming && !busy && !disabled
  const meta = MODE_META[mode]

  return (
    <div className="panel p-2 shadow-card">
      <textarea
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={1}
        disabled={disabled}
        placeholder={placeholder}
        aria-label="消息输入框"
        className={cn(
          'scroll-y max-h-[200px] min-h-touch w-full resize-none bg-transparent px-2 py-2',
          'text-base leading-relaxed text-text placeholder:text-text-faint',
          'focus:outline-none disabled:opacity-50',
        )}
      />

      <div className="mt-1 flex items-center justify-between gap-2">
        {/* 模式切换：流式 / 一次性 */}
        <button
          type="button"
          onClick={() => onModeChange(mode === 'stream' ? 'once' : 'stream')}
          disabled={streaming || busy || disabled}
          title={meta.hint}
          aria-label={`发送模式：${meta.label}。${meta.hint}。点击切换`}
          className={cn(
            'flex min-h-touch items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium transition-colors',
            'border border-border bg-surface-2/60 text-text-muted',
            'hover:border-primary/40 hover:text-text',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          {mode === 'stream' ? (
            <Waves className="size-4 text-primary" aria-hidden />
          ) : (
            <MessageSquare className="size-4 text-primary" aria-hidden />
          )}
          <span>{meta.label}</span>
        </button>

        {/* 两种模式都可中断：流式断 SSE，一次性断 fetch。 */}
        {streaming || busy ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="停止生成"
            className="grid size-11 shrink-0 place-items-center rounded-lg border border-danger/40 bg-danger/10 text-danger transition-colors hover:bg-danger/20"
          >
            <Square className="size-4" aria-hidden />
          </button>
        ) : (
          <button
            type="button"
            onClick={onSubmit}
            disabled={!canSend}
            aria-label={busy ? '正在等待响应' : '发送消息'}
            className={cn(
              'grid size-11 shrink-0 place-items-center rounded-lg transition-all duration-150',
              canSend
                ? 'bg-primary text-primary-fg hover:bg-primary/90 active:scale-95'
                : 'cursor-not-allowed bg-surface-3 text-text-faint',
            )}
          >
            <ArrowUp className="size-4" aria-hidden />
          </button>
        )}
      </div>
    </div>
  )
}
