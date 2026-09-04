/** 顶栏：移动端导航开关、后端连通性指示、主题切换与用户菜单。 */

import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, LogOut, Moon, Sun, UserRound } from 'lucide-react'

import { Badge } from '@/components/ui/Badge'
import { api } from '@/lib/http'
import { ROLE_META } from '@/types/api'
import type { HealthInfo, UserInfo } from '@/types/api'
import { cn } from '@/lib/cn'
import { useThemeStore } from '@/store/theme'

export interface TopBarProps {
  user: UserInfo | null
  onOpenNav: () => void
  onLogout: () => void
}

function ThemeToggle() {
  const theme = useThemeStore((s) => s.theme)
  const toggle = useThemeStore((s) => s.toggle)
  const isDark = theme === 'dark'
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={isDark ? '切换到浅色主题' : '切换到深色主题'}
      title={isDark ? '切换到浅色主题' : '切换到深色主题'}
      className="grid size-11 shrink-0 place-items-center rounded-lg text-text-muted transition-colors hover:bg-surface-2 hover:text-text"
    >
      {isDark ? <Sun className="size-[18px]" aria-hidden /> : <Moon className="size-[18px]" aria-hidden />}
    </button>
  )
}

function UserMenu({ user, onLogout }: { user: UserInfo; onLogout: () => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDocClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    function onEsc(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onEsc)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onEsc)
    }
  }, [open])

  const meta = ROLE_META[user.role]

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex h-11 items-center gap-2 rounded-lg px-2 text-sm transition-colors hover:bg-surface-2 tap-target"
      >
        <span className="grid size-7 shrink-0 place-items-center rounded-full bg-primary/15 text-primary">
          <UserRound className="size-4" aria-hidden />
        </span>
        <span className="hidden max-w-28 truncate text-text sm:inline">{user.username}</span>
        <ChevronDown
          className={cn('size-3.5 text-text-faint transition-transform', open && 'rotate-180')}
          aria-hidden
        />
      </button>

      {open && (
        <div
          role="menu"
          className="panel absolute right-0 top-[calc(100%+6px)] z-50 w-60 animate-fade-up overflow-hidden shadow-pop"
        >
          <div className="space-y-2 border-b border-border px-4 py-3">
            <p className="truncate text-sm font-medium text-text">{user.username}</p>
            {user.email && <p className="truncate text-xs text-text-faint">{user.email}</p>}
            <Badge tone={meta.tone}>{meta.label}</Badge>
          </div>
          <div className="px-4 py-2.5">
            <p className="text-xs text-text-faint">
              租户 <span className="font-mono text-text-muted">{user.tenant_id.slice(0, 12)}…</span>
            </p>
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false)
              onLogout()
            }}
            className="flex min-h-touch w-full items-center gap-2 border-t border-border px-4 py-2.5 text-sm text-danger transition-colors hover:bg-danger/10"
          >
            <LogOut className="size-4" aria-hidden />
            退出登录
          </button>
        </div>
      )}
    </div>
  )
}

function BackendStatus() {
  const { data, isError } = useQuery({
    queryKey: ['health'],
    queryFn: () => api.get<HealthInfo>('/health'),
    refetchInterval: 30_000,
    retry: false,
    staleTime: 20_000,
  })

  const ok = Boolean(data) && !isError

  return (
    <div
      className="hidden items-center gap-2 rounded-lg border border-border bg-surface-2/60 px-2.5 py-1.5 md:flex"
      title={ok ? `后端在线 · ${data?.env ?? ''} · v${data?.version ?? ''}` : '无法连接后端服务'}
    >
      <span
        className={cn(
          'size-2 rounded-full',
          ok ? 'bg-success' : 'bg-danger',
          ok && 'animate-pulse-ring',
        )}
        aria-hidden
      />
      <span className="text-xs text-text-muted">{ok ? '后端在线' : '后端离线'}</span>
    </div>
  )
}

export function TopBar({ user, onOpenNav, onLogout }: TopBarProps) {
  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-2 border-b border-border bg-bg/80 px-3 backdrop-blur-md sm:px-5">
      <button
        type="button"
        onClick={onOpenNav}
        aria-label="打开导航"
        className="grid size-11 shrink-0 place-items-center rounded-lg text-text-muted transition-colors hover:bg-surface-2 hover:text-text lg:hidden"
      >
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
        </svg>
      </button>

      <div className="flex-1" />

      <BackendStatus />
      <ThemeToggle />
      {user && <UserMenu user={user} onLogout={onLogout} />}
    </header>
  )
}
