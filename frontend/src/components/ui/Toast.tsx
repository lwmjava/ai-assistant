/** 全局轻提示：操作反馈不打断流程，3.5 秒后自动消失，可手动关闭。 */

import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'

import { cn } from '@/lib/cn'

type ToastTone = 'success' | 'error' | 'info' | 'warning'

interface ToastItem {
  id: number
  tone: ToastTone
  title: string
  description?: string
}

interface ToastApi {
  success: (title: string, description?: string) => void
  error: (title: string, description?: string) => void
  info: (title: string, description?: string) => void
  warning: (title: string, description?: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

const TONE_STYLE: Record<ToastTone, { border: string; icon: ReactNode }> = {
  success: {
    border: 'border-success/40',
    icon: <CheckCircle2 className="size-4 text-success" aria-hidden />,
  },
  error: {
    border: 'border-danger/40',
    icon: <XCircle className="size-4 text-danger" aria-hidden />,
  },
  info: {
    border: 'border-primary/40',
    icon: <Info className="size-4 text-primary" aria-hidden />,
  },
  warning: {
    border: 'border-warning/40',
    icon: <AlertTriangle className="size-4 text-warning" aria-hidden />,
  },
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])

  const push = useCallback((tone: ToastTone, title: string, description?: string) => {
    const id = Date.now() + Math.random()
    setItems((prev) => [...prev, { id, tone, title, description }])
    window.setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id))
    }, 3500)
  }, [])

  const api = useMemo<ToastApi>(
    () => ({
      success: (t, d) => push('success', t, d),
      error: (t, d) => push('error', t, d),
      info: (t, d) => push('info', t, d),
      warning: (t, d) => push('warning', t, d),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex flex-col items-center gap-2 p-4 sm:inset-x-auto sm:right-4 sm:items-end"
        role="region"
        aria-label="通知"
      >
        {items.map((item) => (
          <div
            key={item.id}
            role="status"
            className={cn(
              'pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-lg border bg-surface/95 px-4 py-3 shadow-pop backdrop-blur',
              'animate-fade-up',
              TONE_STYLE[item.tone].border,
            )}
          >
            <span className="mt-0.5 shrink-0">{TONE_STYLE[item.tone].icon}</span>
            <div className="min-w-0 flex-1 space-y-0.5">
              <p className="text-sm font-medium text-text">{item.title}</p>
              {item.description && (
                <p className="break-words text-sm text-text-muted">{item.description}</p>
              )}
            </div>
            <button
              type="button"
              aria-label="关闭通知"
              onClick={() => setItems((prev) => prev.filter((t) => t.id !== item.id))}
              className="-mr-1 -mt-1 grid size-8 shrink-0 place-items-center rounded-md text-text-faint transition-colors hover:bg-surface-2 hover:text-text"
            >
              <X className="size-3.5" aria-hidden />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast 必须在 ToastProvider 内使用')
  return ctx
}
