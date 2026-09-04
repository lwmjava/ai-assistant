/**
 * 前端权限镜像。
 *
 * 与后端 `app/core/security.py` 的 `ROLE_PERMISSIONS` 保持一致，
 * 仅用于 UI 层的入口显隐与按钮禁用——**真正的权限判定始终在后端**，
 * 这里只做「不展示用户点不动的入口」这一层体验优化。
 * 后端矩阵若变更，本文件必须同步。
 */

import type { Role } from '@/types/api'

type Action = 'read' | 'write' | 'delete'

const ROLE_PERMISSIONS: Record<string, Partial<Record<Action, Role[]>>> = {
  system: {
    read: ['system_admin', 'system_viewer'],
    write: ['system_admin'],
  },
  tenants: {
    read: ['system_admin', 'system_viewer', 'tenant_admin'],
    write: ['system_admin'],
    delete: ['system_admin'],
  },
  members: {
    read: ['system_admin', 'tenant_admin', 'member'],
    write: ['system_admin', 'tenant_admin'],
    delete: ['system_admin', 'tenant_admin'],
  },
  conversations: {
    read: ['system_admin', 'system_viewer', 'tenant_admin', 'member', 'viewer'],
    write: ['system_admin', 'tenant_admin', 'member'],
    delete: ['system_admin', 'tenant_admin', 'member'],
  },
  knowledge_bases: {
    read: ['system_admin', 'system_viewer', 'tenant_admin', 'member', 'viewer'],
    write: ['system_admin', 'tenant_admin', 'member'],
    delete: ['system_admin', 'tenant_admin'],
  },
  agents: {
    read: ['system_admin', 'system_viewer', 'tenant_admin', 'member'],
    write: ['system_admin', 'tenant_admin'],
    delete: ['system_admin', 'tenant_admin'],
  },
  workflows: {
    read: ['system_admin', 'system_viewer', 'tenant_admin', 'member'],
    write: ['system_admin', 'tenant_admin', 'member'],
    delete: ['system_admin', 'tenant_admin'],
  },
}

/** 判断角色是否具备某资源的某操作权限。 */
export function can(role: Role | undefined, resource: string, action: Action): boolean {
  if (!role) return false
  const perms = ROLE_PERMISSIONS[resource]
  if (!perms) return false
  return (perms[action] ?? []).includes(role)
}

/** 审计日志入口：后端限定 system_admin / system_viewer。 */
export function canViewAudit(role: Role | undefined): boolean {
  return role === 'system_admin' || role === 'system_viewer'
}
