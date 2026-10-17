/** 图执行完成后的子任务摘要。没有合格记录时不渲染。 */

import type { SubtaskSummary as SubtaskItem } from '@/types/api'

export function SubtaskSummary({ items }: { items: SubtaskItem[] }) {
  if (items.length === 0) return null
  return (
    <section className="space-y-2" aria-label="子任务摘要">
      <h3 className="text-xs font-medium text-text-muted">子任务摘要</h3>
      <div className="max-h-40 space-y-2 overflow-y-auto pr-1">
        {items.map((item, index) => (
          <div
            key={`${item.name}-${index}`}
            className="rounded-lg border border-border bg-surface-2/70 px-3 py-2"
          >
            <p className="text-xs text-text-muted">
              <span className="font-medium text-text">{item.name}</span>
              <span className="mx-1.5 text-text-faint">·</span>
              <span>完成</span>
            </p>
            <p className="mt-1 whitespace-pre-wrap break-words text-sm text-text">{item.summary}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
