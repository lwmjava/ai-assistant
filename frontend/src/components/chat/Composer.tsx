/** 输入区：自适应高度、Enter 发送 / Shift+Enter 换行、流式/一次性模式切换。 */

import { useEffect, useRef } from 'react'
import type { ClipboardEvent, KeyboardEvent } from 'react'
import { ArrowUp, MessageSquare, Paperclip, Square, Waves, X } from 'lucide-react'

import { cn } from '@/lib/cn'
import { ATTACHMENT_ACCEPT, attachmentSize } from '@/lib/chat-attachments'
import type { AttachmentDraft } from '@/lib/chat-attachments'
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
  /** 限流倒计时未结束：只禁用发送，输入框仍可编辑。 */
  sendLocked?: boolean
  /** 剩余可发送秒数。锁定时显示在发送按钮上。 */
  retryLeft?: number | null
  placeholder?: string
  attachments?: AttachmentDraft[]
  onFiles?: (files: File[]) => void
  onRemoveAttachment?: (id: string) => void
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
  sendLocked = false,
  retryLeft = null,
  placeholder = '输入消息，Enter 发送，Shift+Enter 换行',
  attachments = [],
  onFiles,
  onRemoveAttachment,
}: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const attachmentsReady = attachments.every((item) => item.status === 'ready')
  const hasContent = Boolean(value.trim()) || attachments.some((item) => item.status === 'ready')

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
      if (hasContent && attachmentsReady && !streaming && !busy && !disabled && !sendLocked) onSubmit()
    }
  }

  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    if (busy || streaming || disabled) return
    const files = Array.from(event.clipboardData.items).flatMap((item) => {
      const file = item.kind === 'file' ? item.getAsFile() : null
      return file ? [file] : []
    })
    if (files.length) {
      event.preventDefault()
      onFiles?.(files)
    }
  }

  const canSend = hasContent && attachmentsReady && !streaming && !busy && !disabled && !sendLocked
  const meta = MODE_META[mode]

  return (
    <div className="panel p-2 shadow-card">
      <textarea
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
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

      {attachments.length > 0 && <ul aria-label="本条消息附件" className="space-y-1 px-2 pb-2">
        {attachments.map((item) => <li key={item.id} className="flex items-start gap-2 rounded-lg border border-border bg-surface-2/60 p-2 text-xs">
          <Paperclip className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="break-all">{item.name} <span className="text-text-faint">{attachmentSize(item.size)}</span></p>
            {item.status === 'reading' && <p role="status" className="text-text-muted">正在读取…</p>}
            {item.error && <p role="alert" className="text-danger">{item.error}</p>}
          </div>
          <button type="button" aria-label={`移除 ${item.name}`} disabled={busy || streaming || disabled}
            onClick={() => onRemoveAttachment?.(item.id)} className="grid size-8 shrink-0 place-items-center rounded hover:bg-surface-3 disabled:opacity-50">
            <X className="size-3.5" aria-hidden />
          </button>
        </li>)}
      </ul>}

      <div className="mt-1 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1">
        <input ref={fileRef} type="file" multiple accept={ATTACHMENT_ACCEPT} className="hidden"
          aria-label="选择消息附件" disabled={streaming || busy || disabled}
          onChange={(event) => { onFiles?.(Array.from(event.target.files ?? [])); event.target.value = '' }} />
        <button type="button" onClick={() => fileRef.current?.click()} disabled={streaming || busy || disabled}
          aria-label="添加附件" title="图片、文档或源码，每文件不超过 1 MB"
          className="grid min-h-touch min-w-[44px] place-items-center rounded-lg px-2 text-text-muted hover:bg-surface-2 disabled:opacity-50">
          <Paperclip className="size-4" aria-hidden />
        </button>
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
        </div>

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
        ) : sendLocked ? (
          <button
            type="button"
            disabled
            aria-label={retryLeft != null && retryLeft > 0 ? `${retryLeft} 秒后可重试` : '请稍后重试'}
            className="h-11 shrink-0 cursor-not-allowed rounded-lg bg-surface-3 px-3 text-sm text-text-faint"
          >
            {retryLeft != null && retryLeft > 0 ? `${retryLeft} 秒后可重试` : '请稍后重试'}
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
