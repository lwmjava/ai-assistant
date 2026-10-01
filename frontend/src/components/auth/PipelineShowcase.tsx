/** 管线示意：按顺序点亮阶段节点，直观传达平台的 Agent 编排能力。 */

import { useEffect, useState } from 'react'

import { cn } from '@/lib/cn'
import { PIPELINE_STAGES_BRIEF } from '@/types/api'

export function PipelineShowcase() {
  const [active, setActive] = useState(0)
  const [reduced, setReduced] = useState(false)

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    setReduced(mq.matches)
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    if (reduced) {
      setActive(PIPELINE_STAGES_BRIEF.length - 1)
      return
    }
    const timer = window.setInterval(() => {
      setActive((i) => (i + 1) % (PIPELINE_STAGES_BRIEF.length + 2))
    }, 900)
    return () => window.clearInterval(timer)
  }, [reduced])

  return (
    <div className="relative overflow-hidden" aria-hidden>
      <div className="space-y-3">
        {PIPELINE_STAGES_BRIEF.map((stage, idx) => {
          const done = idx < active
          const current = idx === active
          return (
            <div key={stage} className="flex items-center gap-3">
              <div className="relative flex flex-col items-center">
                <span
                  className={cn(
                    'grid size-8 place-items-center rounded-full border text-xs font-semibold transition-all duration-500',
                    done && 'border-primary/50 bg-primary/20 text-primary',
                    current && 'border-primary bg-primary text-primary-fg scale-110 shadow-glow',
                    !done && !current && 'border-border bg-surface-2 text-text-faint',
                  )}
                >
                  {idx + 1}
                </span>
                {idx < PIPELINE_STAGES_BRIEF.length - 1 && (
                  <span
                    className={cn(
                      'absolute top-9 h-3 w-px transition-colors duration-500',
                      done ? 'bg-primary/60' : 'bg-border',
                    )}
                  />
                )}
              </div>
              <span
                className={cn(
                  'font-display text-sm transition-colors duration-500',
                  current ? 'text-text' : done ? 'text-text-muted' : 'text-text-faint',
                )}
              >
                {stage}
              </span>
              {current && !reduced && (
                <span className="ml-1 h-1 w-8 overflow-hidden rounded-full bg-surface-3">
                  <span className="block h-full w-1/3 animate-shimmer rounded-full bg-primary/70" />
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
