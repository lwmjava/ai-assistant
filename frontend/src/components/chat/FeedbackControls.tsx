import { useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { MessageSquareWarning, ThumbsDown, ThumbsUp } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { failureFeedback, messageRating, removeFeedback } from '@/api/feedback'
import type { AttemptFeedback } from '@/api/feedback'
import { conversationKeys } from '@/api/chat'
import { api } from '@/lib/http'
import { cn } from '@/lib/cn'
import { feedbackKind } from '@/lib/chat-feedback'
import { useAuthStore } from '@/store/auth'
import type { MessageOut } from '@/types/api'

const REASONS = [
  ['off_topic', '答非所问'], ['factual', '事实错误'], ['format', '格式不符'],
  ['citation', '引用问题'], ['incomplete', '内容不完整'], ['other', '其他'],
]

function VoteButton({
  label, pressed, disabled, icon: Icon, tone, onClick,
}: {
  label: string
  pressed: boolean
  disabled: boolean
  icon: LucideIcon
  tone: 'up' | 'down'
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={pressed}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'inline-flex h-5 w-5 items-center justify-center rounded text-text-muted hover:bg-surface-2 hover:text-text disabled:cursor-not-allowed disabled:opacity-50',
        pressed && (tone === 'up' ? 'bg-primary/10 text-primary' : 'bg-danger/10 text-danger'),
      )}
    >
      <Icon className="h-3 w-3" strokeWidth={pressed ? 2.25 : 1.75} />
    </button>
  )
}

export function FeedbackControls({ message }: { message: MessageOut }) {
  const qc = useQueryClient()
  const kind = feedbackKind(message)
  const [open, setOpen] = useState(false)
  const [rating, setRating] = useState<'up' | 'down'>(message.rating ?? 'up')
  const [savedRating, setSavedRating] = useState(message.rating ?? null)
  const [recordId, setRecordId] = useState(message.feedback_id ?? message.rating_id ?? null)
  const [submitted, setSubmitted] = useState(Boolean(message.feedback_submitted || message.rating))
  const [reason, setReason] = useState('')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function refresh() {
    await qc.invalidateQueries({ queryKey: conversationKeys.all })
    await qc.invalidateQueries({ queryKey: ['feedback-attempt'] })
  }

  async function remove() {
    if (!recordId || !kind) return
    setBusy(true); setError(null)
    try {
      await removeFeedback(kind, recordId)
      setRecordId(null); setSubmitted(false); setSavedRating(null); setOpen(false); setReason(''); setComment('')
      await refresh()
    } catch { setError('撤销失败，请重试。') } finally { setBusy(false) }
  }

  function onDown() {
    if (open && rating === 'down') {
      if (savedRating === 'down' && recordId) void remove()
      else { setOpen(false); setReason(''); setComment('') }
      return
    }
    setRating('down')
    setOpen(true)
  }

  async function chooseUp() {
    if (savedRating === 'up' && recordId) {
      await remove()
      return
    }
    setBusy(true); setError(null); setRating('up'); setOpen(false)
    try {
      const saved = await messageRating(message.id, 'up', '', '')
      setRecordId(saved.id); setSubmitted(true); setSavedRating('up'); setReason(''); setComment('')
      await refresh()
    } catch {
      setError('提交失败，请重试。')
    } finally { setBusy(false) }
  }

  async function submit() {
    setBusy(true); setError(null)
    try {
      const saved = kind === 'failure'
        ? await failureFeedback(message.attempt_id!, message.attempt_outcome!, comment)
        : await messageRating(message.id, 'down', reason, comment)
      setRecordId(saved.id); setSubmitted(true); setOpen(false); setComment('')
      if (kind === 'rating') setSavedRating('down')
      await refresh()
    } catch {
      setError('提交失败，请重试；评论草稿已保留。')
    } finally { setBusy(false) }
  }

  if (!kind) return null
  const status = kind === 'failure'
    ? (submitted ? '已反馈' : '')
    : savedRating === 'up' ? '已点赞' : savedRating === 'down' ? '已点踩' : ''

  return <div className="mt-2 space-y-2 text-sm" aria-label="对话反馈">
    <div className="flex items-center gap-0.5">
      {kind === 'failure' ? (
        <VoteButton
          label={submitted ? '撤销问题反馈' : '反馈问题'}
          pressed={submitted}
          disabled={busy}
          icon={MessageSquareWarning}
          tone="down"
          onClick={() => { if (submitted) void remove(); else setOpen(!open) }}
        />
      ) : (
        <>
          <VoteButton label={savedRating === 'up' ? '撤销点赞' : '点赞'} pressed={savedRating === 'up'} disabled={busy} icon={ThumbsUp} tone="up" onClick={() => { void chooseUp() }} />
          <VoteButton label={open && rating === 'down' && savedRating === 'down' ? '撤销点踩' : '点踩'} pressed={savedRating === 'down' || (open && rating === 'down')} disabled={busy} icon={ThumbsDown} tone="down" onClick={onDown} />
        </>
      )}
      {status && <span role="status" className="sr-only">{status}</span>}
    </div>
    {open && <form onSubmit={(e) => { e.preventDefault(); void submit() }} className="space-y-2">
      {kind === 'rating' && <label className="block text-text-muted">原因（选填）
        <select value={reason} onChange={(e) => setReason(e.target.value)} className="ml-2 rounded border border-border bg-surface p-2">
          <option value="">暂不选择</option>{REASONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
      </label>}
      <label className="block text-text-muted">补充说明（选填，最多500字）
        <textarea maxLength={500} value={comment} onChange={(e) => setComment(e.target.value)} disabled={busy}
          autoFocus placeholder="可以补充这次回复的问题" className="mt-1 block w-full rounded border border-border bg-surface p-2" rows={3} />
      </label>
      <p className="text-xs text-text-muted">保存前会脱敏；请勿填写密码、密钥或个人敏感信息。反馈不会自动修改模型。</p>
      <button type="submit" disabled={busy} className="rounded bg-primary px-3 py-1.5 text-white disabled:opacity-50">{busy ? '提交中…' : '保存说明'}</button>
    </form>}
    {error && <p role="alert" className="text-danger">{error}</p>}
  </div>
}

export function AttemptFeedbackControls({ attemptId }: { attemptId: string }) {
  const user = useAuthStore((s) => s.user)
  const id = attemptId.replaceAll('-', '')
  const query = useQuery({
    queryKey: ['feedback-attempt', user?.id, user?.tenant_id, user?.role, id],
    queryFn: () => api.get<AttemptFeedback>(`/chat/attempts/${encodeURIComponent(id)}`),
    enabled: /^[0-9a-f]{32}$/.test(id),
  })
  if (!query.data || !['api_error', 'no_response'].includes(query.data.outcome)) return null
  return <div className="mx-auto max-w-4xl px-3 pb-3"><FeedbackControls key={`${id}-${query.data.feedback_id}`} message={{
    id, role: 'user', content: '', model: null, created_at: '', attempt_id: id,
    attempt_outcome: query.data.outcome, feedback_submitted: query.data.feedback_submitted,
    feedback_id: query.data.feedback_id,
  }} /></div>
}
