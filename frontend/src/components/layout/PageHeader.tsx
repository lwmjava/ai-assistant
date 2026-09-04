/** 页面标题区：统一的标题层级与右侧操作区，移动端自动塌缩。 */

import type { ReactNode } from 'react'

import { cn } from '@/lib/cn'

export interface PageHeaderProps {
  title: string
  description?: string
  actions?: ReactNode
  className?: string
}

export function PageHeader({ title, description, actions, className }: PageHeaderProps) {
  return (
    <div className={cn('mb-5 flex flex-col gap-3 sm:mb-6 sm:flex-row sm:items-end sm:justify-between', className)}>
      <div className="space-y-1">
        <h1 className="font-display text-2xl font-semibold tracking-tight text-text sm:text-3xl">
          {title}
        </h1>
        {description && (
          <p className="max-w-2xl text-pretty text-sm text-text-muted sm:text-base">{description}</p>
        )}
      </div>
      {actions && (
        <div className="flex flex-wrap items-center gap-2 sm:justify-end">{actions}</div>
      )}
    </div>
  )
}
