/** 侧边栏导航：按当前角色权限过滤入口，移动端以抽屉形式呈现。 */

import { NavLink } from 'react-router-dom'
import {
  BookOpen,
  CalendarClock,
  MessagesSquare,
  ShieldCheck,
  Wrench,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { can, canViewAudit } from '@/lib/permissions'
import { cn } from '@/lib/cn'
import type { Role } from '@/types/api'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  /** 该入口所需的权限；不满足则隐藏。 */
  resource?: string
  action?: 'read' | 'write' | 'delete'
  /** 不满足上面条件时的备用判定（如审计日志只看角色）。 */
  visible?: (role: Role | undefined) => boolean
}

const ITEMS: NavItem[] = [
  { to: '/chat', label: '对话', icon: MessagesSquare, resource: 'conversations', action: 'read' },
  { to: '/knowledge', label: '知识库', icon: BookOpen, resource: 'knowledge_bases', action: 'read' },
  { to: '/tools', label: '工具与 MCP', icon: Wrench, resource: 'agents', action: 'read' },
  { to: '/workflows', label: '工作流', icon: CalendarClock, resource: 'workflows', action: 'read' },
  { to: '/audit', label: '审计日志', icon: ShieldCheck, visible: (role) => canViewAudit(role) },
]

export interface SidebarProps {
  role: Role | undefined
  /** 移动端抽屉是否展开。 */
  open: boolean
  onClose: () => void
}

export function Sidebar({ role, open, onClose }: SidebarProps) {
  const items = ITEMS.filter((item) =>
    item.visible ? item.visible(role) : can(role, item.resource!, item.action!),
  )

  return (
    <>
      {/* 遮罩：仅移动端抽屉展开时可见 */}
      {open && (
        <div
          className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm lg:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}

      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-border bg-surface/95 backdrop-blur',
          'transition-transform duration-250 lg:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
        aria-label="主导航"
      >
        <div className="flex h-16 items-center justify-between gap-2 border-b border-border px-4">
          <div className="flex min-w-0 items-center gap-2.5">
            <span
              className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/15 text-primary ring-1 ring-primary/30"
              aria-hidden
            >
              <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 3 4 7v6c0 4.4 3.4 7.4 8 8 4.6-.6 8-3.6 8-8V7l-8-4Z" strokeLinejoin="round" />
                <path d="M9 12.5l2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <div className="min-w-0">
              <p className="truncate font-display text-sm font-semibold tracking-tight text-text">
                ai-assistant
              </p>
              <p className="truncate text-xs text-text-faint">企业 AI 助手控制台</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭导航"
            className="grid size-11 shrink-0 place-items-center rounded-lg text-text-faint hover:bg-surface-2 hover:text-text lg:hidden"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>

        <nav className="scroll-y flex-1 space-y-1 p-3">
          {items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={onClose}
              className={({ isActive }) =>
                cn(
                  'group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors',
                  'min-h-touch',
                  isActive
                    ? 'bg-primary/12 font-medium text-primary ring-1 ring-primary/25'
                    : 'text-text-muted hover:bg-surface-2 hover:text-text',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon className="size-[18px] shrink-0" aria-hidden />
                  <span className="truncate">{item.label}</span>
                  {isActive && (
                    <span className="ml-auto size-1.5 rounded-full bg-primary" aria-hidden />
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-border p-3">
          <p className="px-1 text-xs leading-relaxed text-text-faint">
            权限由后端角色矩阵裁定，界面仅隐藏当前角色不可访问的能力。
          </p>
        </div>
      </aside>
    </>
  )
}
