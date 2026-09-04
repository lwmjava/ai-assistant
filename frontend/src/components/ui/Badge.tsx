/** 徽章：状态、角色、计数等短标签。 */

import type { ReactNode } from 'react'

import { cn } from '@/lib/cn'

type Tone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'accent'

const TONES: Record<Tone, string> = {
  neutral: 'bg-surface-3/80 text-text-muted border-border',
  primary: 'bg-primary/15 text-primary border-primary/35',
  success: 'bg-success/15 text-success border-success/35',
  warning: 'bg-warning/15 text-warning border-warning/35',
  danger: 'bg-danger/15 text-danger border-danger/35',
  accent: 'bg-accent/15 text-accent border-accent/35',
}

export interface BadgeProps {
  tone?: Tone
  children: ReactNode
  className?: string
  /** 状态圆点（用于执行状态等需要强调「运行中」的场景）。 */
  dot?: boolean
  pulse?: boolean
  /** 原生 tooltip：用于缩写或需要补充说明的状态。 */
  title?: string
}

export function Badge({ tone = 'neutral', children, className, dot, pulse, title }: BadgeProps) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-2 py-0.5 text-xs font-medium',
        TONES[tone],
        className,
      )}
    >
      {dot && (
        <span
          className={cn(
            'size-1.5 rounded-full bg-current',
            pulse && 'animate-pulse-ring',
          )}
          aria-hidden
        />
      )}
      {children}
    </span>
  )
}
