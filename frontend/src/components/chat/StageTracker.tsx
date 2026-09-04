/**
 * 管线阶段指示器：把 Agent 的五阶段执行过程可视化。
 *
 * 后端会按实际路径发送阶段（可能跳过「检索」，也可能插入「意图分流」
 * 与「质量门自纠错」），这里以标准顺序为骨架，动态合入实际出现的阶段。
 */

import { useMemo } from 'react'
import { Check, Loader2, Wrench } from 'lucide-react'

import { cn } from '@/lib/cn'
import { PIPELINE_STAGES, PIPELINE_STAGES_BRIEF } from '@/types/api'

export interface StageTrackerProps {
  /** 已收到的阶段序列（按发生顺序）。 */
  stages: string[]
  currentStage: string | null
  tools: string[]
  streaming: boolean
  className?: string
}

/** 合并标准顺序与实际阶段：标准内按固定序，标准外（后端新增阶段）按出现顺序追加。 */
function mergeStages(received: string[]): string[] {
  const extra = received.filter((s) => !PIPELINE_STAGES.includes(s as (typeof PIPELINE_STAGES)[number]))
  const ordered = PIPELINE_STAGES.filter((s) => received.includes(s))
  return [...ordered, ...extra]
}

export function StageTracker({
  stages,
  currentStage,
  tools,
  streaming,
  className,
}: StageTrackerProps) {
  const list = useMemo(
    // 未开始时用简要序列占位，让用户在发送前就知道会有哪几步
    () => (stages.length ? mergeStages(stages) : [...PIPELINE_STAGES_BRIEF]),
    [stages],
  )
  const started = stages.length > 0

  return (
    <div className={cn('space-y-2', className)}>
      <div className="flex flex-wrap items-center gap-1.5">
        {list.map((stage, idx) => {
          const isCurrent = streaming && stage === currentStage
          // 未真正开始时全部置灰；开始后，当前阶段之前的算完成
          const isDone = started && !isCurrent && stages.indexOf(stage) < stages.indexOf(currentStage ?? '')
          const isPending = !started || (!isCurrent && !isDone)
          const showArrow = idx < list.length - 1

          return (
            <div key={stage} className="flex items-center gap-1.5">
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-all duration-300',
                  isCurrent && 'border-primary/50 bg-primary/15 text-primary',
                  isDone && 'border-success/40 bg-success/10 text-success',
                  isPending && 'border-border bg-surface-2/60 text-text-faint',
                )}
                aria-current={isCurrent ? 'step' : undefined}
              >
                {isCurrent && <Loader2 className="size-3 animate-spin" aria-hidden />}
                {isDone && <Check className="size-3" aria-hidden />}
                {stage}
              </span>
              {showArrow && (
                <span
                  className={cn('text-xs', isDone ? 'text-success/60' : 'text-text-faint/50')}
                  aria-hidden
                >
                  →
                </span>
              )}
            </div>
          )
        })}
      </div>

      {tools.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {tools.map((tool, i) => (
            <span
              key={`${tool}-${i}`}
              className="inline-flex items-center gap-1 rounded-md border border-accent/35 bg-accent/10 px-2 py-0.5 text-xs text-accent"
            >
              <Wrench className="size-3" aria-hidden />
              {tool}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
