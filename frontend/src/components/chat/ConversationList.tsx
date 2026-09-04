/** 会话列表：新建、切换、删除；移动端以抽屉呈现。 */

import { MessageSquarePlus, Trash2, X } from 'lucide-react'

import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { cn } from '@/lib/cn'
import { timeAgo, truncate } from '@/lib/cn'
import { can } from '@/lib/permissions'
import type { ConversationOut, Role } from '@/types/api'

export interface ConversationListProps {
  conversations: ConversationOut[]
  activeId: string | null
  loading: boolean
  error: unknown
  role: Role | undefined
  open: boolean
  onClose: () => void
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
  onRetry: () => void
}

export function ConversationList({
  conversations,
  activeId,
  loading,
  error,
  role,
  open,
  onClose,
  onSelect,
  onNew,
  onDelete,
  onRetry,
}: ConversationListProps) {
  const canDelete = can(role, 'conversations', 'delete')

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm xl:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}

      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-border bg-surface/95 backdrop-blur xl:static xl:z-0 xl:translate-x-0',
          'transition-transform duration-250',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
        aria-label="会话列表"
      >
        <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-3">
          <p className="px-1 text-sm font-medium text-text-muted">会话</p>
          <div className="flex items-center gap-1">
            <Button size="sm" variant="primary" onClick={onNew} icon={<MessageSquarePlus className="size-4" aria-hidden />}>
              新建
            </Button>
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭会话列表"
              className="grid size-11 place-items-center rounded-lg text-text-faint hover:bg-surface-2 hover:text-text xl:hidden"
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>
        </div>

        <div className="scroll-y flex-1 p-2">
          {loading ? (
            <SkeletonRows rows={6} className="p-2" />
          ) : error ? (
            <ErrorState error={error} onRetry={onRetry} className="border-0 bg-transparent" />
          ) : conversations.length === 0 ? (
            <EmptyState
              title="还没有会话"
              description="点击「新建」发起第一轮对话。"
              className="py-10"
            />
          ) : (
            <ul className="space-y-1">
              {conversations.map((conv) => {
                const isActive = conv.id === activeId
                return (
                  <li key={conv.id} className="group/item relative">
                    <button
                      type="button"
                      onClick={() => onSelect(conv.id)}
                      aria-current={isActive ? 'true' : undefined}
                      className={cn(
                        'w-full rounded-lg px-3 py-2.5 text-left transition-colors',
                        'min-h-touch pr-11',
                        isActive
                          ? 'bg-primary/12 ring-1 ring-primary/25'
                          : 'hover:bg-surface-2',
                      )}
                    >
                      <span
                        className={cn(
                          'block truncate text-sm',
                          isActive ? 'font-medium text-text' : 'text-text-muted',
                        )}
                      >
                        {truncate(conv.title || '未命名会话', 28)}
                      </span>
                      <span className="mt-0.5 block text-xs text-text-faint">
                        {timeAgo(conv.updated_at)}
                      </span>
                    </button>

                    {canDelete && (
                      <button
                        type="button"
                        onClick={() => onDelete(conv.id)}
                        aria-label={`删除会话 ${conv.title ?? conv.id}`}
                        className={cn(
                          'absolute right-1 top-1/2 grid size-9 -translate-y-1/2 place-items-center rounded-md',
                          'text-text-faint opacity-0 transition-opacity hover:bg-danger/15 hover:text-danger',
                          'focus-visible:opacity-100 group-hover/item:opacity-100',
                        )}
                      >
                        <Trash2 className="size-3.5" aria-hidden />
                      </button>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </aside>
    </>
  )
}
