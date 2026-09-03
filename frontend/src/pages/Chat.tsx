/** 对话页：会话列表 + 流式消息流 + 管线阶段可视化。 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { MessagesSquare, PanelLeft, Sparkles } from 'lucide-react'

import { Composer } from '@/components/chat/Composer'
import { ConversationList } from '@/components/chat/ConversationList'
import { MessageList } from '@/components/chat/MessageList'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { EmptyState } from '@/components/ui/Feedback'
import { useToast } from '@/components/ui/Toast'
import {
  conversationKeys,
  sendMessage,
  useConversation,
  useConversations,
  useDeleteConversation,
} from '@/api/chat'
import { useChatStream } from '@/hooks/useChatStream'
import { api, ApiError } from '@/lib/http'
import { useAuthStore } from '@/store/auth'
import type { ConversationDetail, ConversationOut, MessageOut, SendMode } from '@/types/api'

/**
 * 后端流式响应在发出 done 之前才落库消息，刷新需留出这段窗口。
 * 非流式（POST /chat）在返回前已 commit，无需等待，因此不走这个延迟。
 */
const PERSIST_DELAY_MS = 600

/** 发送模式记忆在本地：这是纯界面偏好，不进后端。 */
const MODE_STORAGE_KEY = 'aa-chat-mode'

function readMode(): SendMode {
  try {
    return localStorage.getItem(MODE_STORAGE_KEY) === 'once' ? 'once' : 'stream'
  } catch {
    return 'stream'
  }
}

function WelcomeHero({ onPick }: { onPick: (text: string) => void }) {
  const samples = [
    '总结一下知识库里关于产品定价的内容',
    '帮我起草一封项目延期说明邮件',
    '用计算器算一下 128 × 47 再加上 15%',
  ]
  return (
    <div className="mx-auto flex max-w-xl flex-col items-center gap-6 px-6 py-12 text-center">
      <span className="grid size-14 place-items-center rounded-2xl border border-primary/25 bg-primary/10 text-primary">
        <Sparkles className="size-6" aria-hidden />
      </span>
      <div className="space-y-2">
        <h2 className="font-display text-2xl font-semibold tracking-tight text-text text-balance">
          从一句话开始
        </h2>
        <p className="text-sm text-text-muted text-pretty">
          助手会按需调用工具与知识库，并在回答时展示完整的推理阶段。
        </p>
      </div>
      <div className="grid w-full gap-2 sm:grid-cols-1">
        {samples.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="min-h-touch rounded-xl border border-border bg-surface/70 px-4 py-3 text-left text-sm text-text-muted transition-colors hover:border-primary/40 hover:bg-surface-2 hover:text-text"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

export default function ChatPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const role = useAuthStore((s) => s.user?.role)

  const [activeId, setActiveId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [pending, setPending] = useState<MessageOut[]>([])
  const [listOpen, setListOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ConversationOut | null>(null)
  /** 发送模式：流式 / 一次性，记忆在本地。 */
  const [mode, setMode] = useState<SendMode>(readMode)
  /** 一次性模式等待中（后端不回传任何中间态）。 */
  const [awaiting, setAwaiting] = useState(false)

  // 最近一次发送的内容，失败时回填输入框
  const lastSentRef = useRef('')
  // 一次性模式的中断控制器；fetch 被 abort 会抛 AbortError，需与真实错误区分
  const onceAbortRef = useRef<AbortController | null>(null)

  const conversationsQuery = useConversations()
  const detailQuery = useConversation(activeId)
  const deleteMutation = useDeleteConversation()
  const { snapshot, send, stop, reset } = useChatStream()

  const handleModeChange = useCallback((next: SendMode) => {
    setMode(next)
    try {
      localStorage.setItem(MODE_STORAGE_KEY, next)
    } catch {
      /* 隐私模式下 localStorage 不可写，仅本次会话生效 */
    }
  }, [])

  const busy = snapshot.streaming || awaiting

  const conversations = conversationsQuery.data ?? []
  const serverMessages = useMemo(() => detailQuery.data?.messages ?? [], [detailQuery.data])

  // 合并：服务端已落库消息 + 本次尚未落库的用户消息
  const messages = useMemo(() => {
    if (!pending.length) return serverMessages
    const seen = new Set(serverMessages.map((m) => m.content))
    const notPersisted = pending.filter((p) => !seen.has(p.content))
    return [...serverMessages, ...notPersisted]
  }, [serverMessages, pending])

  // 流式失败：保留用户输入，仅提示错误
  useEffect(() => {
    if (!snapshot.error) return
    if (lastSentRef.current) {
      setInput(lastSentRef.current)
      lastSentRef.current = ''
    }
    setPending([])
    toast.error('生成失败', snapshot.error)
    reset()
  }, [snapshot.error, toast, reset])

  const refreshAfterStream = useCallback(
    async (conversationId: string | null) => {
      await new Promise((r) => setTimeout(r, PERSIST_DELAY_MS))
      await qc.invalidateQueries({ queryKey: conversationKeys.all })
      if (conversationId) {
        await qc.refetchQueries({ queryKey: conversationKeys.detail(conversationId) })
        return
      }
      // 新建会话：后端流式响应不回传 id，用最新更新的会话定位
      const list = await qc.fetchQuery({
        queryKey: conversationKeys.list(),
        queryFn: () => conversationsQuery.refetch().then((r) => r.data ?? []),
      })
      const newest = [...list].sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0]
      if (newest) setActiveId(newest.id)
    },
    [qc, conversationsQuery],
  )

  /** 一次性模式：响应里就带 conversation_id，直接定位，无需等落库后按时间猜。 */
  const applyOnceResult = useCallback(
    async (conversationId: string) => {
      await qc.invalidateQueries({ queryKey: conversationKeys.all })
      setActiveId(conversationId)
      await qc.fetchQuery({
        queryKey: conversationKeys.detail(conversationId),
        queryFn: () => api.get<ConversationDetail>(`/chat/conversations/${conversationId}`),
      })
    },
    [qc],
  )

  const handleSend = useCallback(async () => {
    const text = input.trim()
    if (!text || busy) return

    setInput('')
    lastSentRef.current = text
    reset()

    const optimistic: MessageOut = {
      id: `pending-${Date.now()}`,
      role: 'user',
      content: text,
      model: null,
      created_at: new Date().toISOString(),
    }
    setPending([optimistic])

    if (mode === 'stream') {
      await send({
        message: text,
        conversationId: activeId,
        onFinished: async () => {
          lastSentRef.current = ''
          await refreshAfterStream(activeId)
          setPending([])
          reset()
        },
      })
      return
    }

    // 一次性模式：等待完整结果
    const controller = new AbortController()
    onceAbortRef.current = controller
    setAwaiting(true)
    try {
      const res = await sendMessage(
        { message: text, conversation_id: activeId },
        controller.signal,
      )
      lastSentRef.current = ''
      setPending([])
      await applyOnceResult(res.conversation_id)
    } catch (err) {
      // 用户主动中断：静默收尾，不弹错误、不回填（内容已在输入框清空，按中断语义丢弃）
      if (err instanceof DOMException && err.name === 'AbortError') return
      if (lastSentRef.current) {
        setInput(lastSentRef.current)
        lastSentRef.current = ''
      }
      setPending([])
      toast.error('生成失败', err instanceof ApiError ? err.detail : undefined)
    } finally {
      onceAbortRef.current = null
      setAwaiting(false)
    }
  }, [
    input,
    busy,
    reset,
    mode,
    send,
    activeId,
    refreshAfterStream,
    applyOnceResult,
    toast,
  ])

  /** 中断：流式走 SSE 的 abort，一次性走 fetch 的 abort。 */
  const handleStop = useCallback(() => {
    if (snapshot.streaming) stop()
    else if (awaiting) onceAbortRef.current?.abort()
  }, [snapshot.streaming, awaiting, stop])

  const handleNew = useCallback(() => {
    handleStop()
    reset()
    setPending([])
    setActiveId(null)
    setInput('')
    setListOpen(false)
  }, [handleStop, reset])

  const handleSelect = useCallback(
    (id: string) => {
      if (busy) return
      reset()
      setPending([])
      setActiveId(id)
      setListOpen(false)
    },
    [busy, reset],
  )

  const confirmDelete = useCallback(async () => {
    if (!pendingDelete) return
    try {
      await deleteMutation.mutateAsync(pendingDelete.id)
      if (activeId === pendingDelete.id) {
        setActiveId(null)
        setPending([])
        reset()
      }
      toast.success('会话已删除')
    } catch (err) {
      toast.error('删除失败', err instanceof ApiError ? err.detail : undefined)
    } finally {
      setPendingDelete(null)
    }
  }, [pendingDelete, deleteMutation, activeId, toast, reset])

  const detailError = detailQuery.error

  return (
    <div className="-mx-3 -my-5 flex h-[calc(100dvh-4rem)] overflow-hidden sm:-mx-6 sm:-my-7">
      <ConversationList
        conversations={conversations}
        activeId={activeId}
        loading={conversationsQuery.isLoading}
        error={conversationsQuery.error}
        role={role}
        open={listOpen}
        onClose={() => setListOpen(false)}
        onSelect={handleSelect}
        onNew={handleNew}
        onDelete={(id) => {
          const target = conversations.find((c) => c.id === id)
          if (target) setPendingDelete(target)
        }}
        onRetry={() => void conversationsQuery.refetch()}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* 会话区顶栏：移动端打开列表 + 新建 */}
        <div className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-3">
          <button
            type="button"
            onClick={() => setListOpen(true)}
            aria-label="打开会话列表"
            className="grid size-11 place-items-center rounded-lg text-text-muted hover:bg-surface-2 hover:text-text xl:hidden"
          >
            <PanelLeft className="size-[18px]" aria-hidden />
          </button>
          <p className="min-w-0 flex-1 truncate text-sm text-text-muted">
            {activeId
              ? (conversations.find((c) => c.id === activeId)?.title ?? '当前会话')
              : '新会话'}
          </p>
          <Button
            size="sm"
            variant="ghost"
            onClick={handleNew}
            icon={<MessagesSquare className="size-4" aria-hidden />}
          >
            新建
          </Button>
        </div>

        <div className="scroll-y flex-1">
          {detailError ? (
            <EmptyState
              icon={<MessagesSquare className="size-5" aria-hidden />}
              title="无法加载会话"
              description={
                detailError instanceof ApiError ? detailError.detail : '会话不存在或无权访问'
              }
              action={
                <Button size="sm" onClick={handleNew}>
                  新建一个会话
                </Button>
              }
            />
          ) : messages.length === 0 && !busy && !snapshot.text ? (
            <WelcomeHero onPick={(text) => setInput(text)} />
          ) : (
            <MessageList
              messages={messages}
              streamingText={snapshot.text}
              stageStages={snapshot.stages}
              currentStage={snapshot.currentStage}
              tools={snapshot.tools}
              streaming={snapshot.streaming}
              awaiting={awaiting}
              streamError={snapshot.error}
              className="mx-auto max-w-4xl px-3 sm:px-6"
            />
          )}
        </div>

        <div className="shrink-0 border-t border-border/60 bg-bg/60 p-3 backdrop-blur sm:p-4">
          <div className="mx-auto max-w-4xl">
            <Composer
              value={input}
              onChange={setInput}
              onSubmit={() => void handleSend()}
              onStop={handleStop}
              streaming={snapshot.streaming}
              busy={awaiting}
              mode={mode}
              onModeChange={handleModeChange}
            />
            <p className="mt-2 px-1 text-xs text-text-faint">
              {mode === 'stream'
                ? '流式模式：实时展示 Agent 管线阶段与工具调用，可随时中断。'
                : '一次性模式：等待完整结果后一次返回，看不到中间过程，但能直接定位会话。'}
              内容均经过输入过滤与注入检测。
            </p>
          </div>
        </div>
      </div>

      <Modal
        open={Boolean(pendingDelete)}
        onClose={() => setPendingDelete(null)}
        title="删除会话"
        description="会话内的全部消息将一并移除，此操作不可撤销。"
        size="sm"
        busy={deleteMutation.isPending}
        footer={
          <>
            <Button variant="ghost" onClick={() => setPendingDelete(null)}>
              取消
            </Button>
            <Button variant="danger" loading={deleteMutation.isPending} onClick={() => void confirmDelete()}>
              确认删除
            </Button>
          </>
        }
      >
        <p className="text-sm text-text-muted">
          {truncateTitle(pendingDelete?.title)}
        </p>
      </Modal>
    </div>
  )
}

function truncateTitle(title: string | null | undefined) {
  if (!title) return '未命名会话'
  return title.length > 60 ? `${title.slice(0, 60)}…` : title
}
