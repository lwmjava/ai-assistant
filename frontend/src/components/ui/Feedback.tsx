/** 空态、错误态与加载占位：所有异步区域都需要这三种状态。 */

import type { ReactNode } from 'react'
import { AlertTriangle, Inbox, RefreshCw } from 'lucide-react'

import { Button } from './Button'
import { cn } from '@/lib/cn'

export interface EmptyStateProps {
  icon?: ReactNode
  title: string
  description?: string
  action?: ReactNode
  className?: string
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 px-6 py-14 text-center',
        className,
      )}
    >
      <div className="grid size-12 place-items-center rounded-xl border border-border bg-surface-2/70 text-text-faint">
        {icon ?? <Inbox className="size-5" aria-hidden />}
      </div>
      <div className="space-y-1">
        <p className="font-display text-base font-semibold text-text">{title}</p>
        {description && (
          <p className="mx-auto max-w-sm text-sm text-text-muted">{description}</p>
        )}
      </div>
      {action}
    </div>
  )
}

export interface ErrorStateProps {
  /** 错误标题；未传时根据错误内容推断。 */
  title?: string
  error: unknown
  onRetry?: () => void
  className?: string
}

/** 统一错误态：区分「模块未启用(503)」「无权限(403)」与普通失败，避免一句「出错了」。 */
export function ErrorState({ title, error, onRetry, className }: ErrorStateProps) {
  const status = (error as { status?: number })?.status
  const message = (error as { message?: string })?.message ?? '未知错误'

  const heading =
    title ??
    (status === 503
      ? '该模块未在服务端启用'
      : status === 403
        ? '当前角色无权访问此资源'
        : '加载失败')

  const hint =
    status === 503
      ? '请在后端环境变量中启用对应开关后重启服务，例如 WORKFLOW_ENABLED=true。'
      : status === 403
        ? '权限由后端角色矩阵控制，如需访问请联系平台管理员调整角色。'
        : message

  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-card border border-danger/30 bg-danger/5 px-6 py-10 text-center',
        className,
      )}
    >
      <div className="grid size-12 place-items-center rounded-xl border border-danger/30 bg-danger/10 text-danger">
        <AlertTriangle className="size-5" aria-hidden />
      </div>
      <div className="space-y-1">
        <p className="font-display text-base font-semibold text-text">{heading}</p>
        <p className="mx-auto max-w-md text-sm text-text-muted">{hint}</p>
      </div>
      {onRetry && (
        <Button size="sm" onClick={onRetry} icon={<RefreshCw className="size-3.5" aria-hidden />}>
          重试
        </Button>
      )}
    </div>
  )
}

/** 列表与卡片的骨架占位。 */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('skeleton h-4 rounded-md', className)} aria-hidden />
}

export function SkeletonRows({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('space-y-3', className)} aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="size-8 shrink-0 rounded-lg" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-3.5 w-[45%]" />
            <Skeleton className="h-3 w-[70%]" />
          </div>
        </div>
      ))}
    </div>
  )
}
