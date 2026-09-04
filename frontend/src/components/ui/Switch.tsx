/** 开关：用于工作流的启用/禁用等即时生效的布尔设置。 */

import { cn } from '@/lib/cn'

export interface SwitchProps {
  checked: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
  label: string
  /** 隐藏可见标签（用于表格内紧凑布局，仍保留 aria-label）。 */
  hideLabel?: boolean
  className?: string
}

export function Switch({
  checked,
  onChange,
  disabled,
  label,
  hideLabel = false,
  className,
}: SwitchProps) {
  return (
    <label
      className={cn(
        'inline-flex cursor-pointer items-center gap-2.5 select-none',
        disabled && 'cursor-not-allowed opacity-50',
        className,
      )}
    >
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative h-6 w-11 shrink-0 rounded-full border transition-colors duration-200',
          'focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-bg',
          checked ? 'border-primary/60 bg-primary/80' : 'border-border bg-surface-3',
        )}
      >
        <span
          className={cn(
            'absolute top-1/2 size-4 -translate-y-1/2 rounded-full bg-white shadow-sm transition-transform duration-200',
            checked ? 'translate-x-6' : 'translate-x-1',
          )}
        />
      </button>
      {!hideLabel && <span className="text-sm text-text-muted">{label}</span>}
    </label>
  )
}
