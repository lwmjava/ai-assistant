/**
 * 前端权限镜像。
 *
 * 与后端 `app/core/security.py` 的 `ROLE_PERMISSIONS` 保持一致，
 * 仅用于 UI 层的入口显隐与按钮禁用——**真正的权限判定始终在后端**，
 * 这里只做「不展示用户点不动的入口」这一层体验优化。
 * 后端矩阵若变更，本文件必须同步。
 */

import type { DocumentOut, Role, UserInfo } from '@/types/api'

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
  skills: {
    read: ['system_admin', 'system_viewer', 'tenant_admin', 'member', 'viewer'],
    write: ['system_admin', 'tenant_admin', 'member'],
    delete: ['system_admin', 'member'],
  },
}

/** 判断角色是否具备某资源的某操作权限。 */
export function can(role: Role | undefined, resource: string, action: Action): boolean {
  if (!role) return false
  const perms = ROLE_PERMISSIONS[resource]
  if (!perms) return false
  return (perms[action] ?? []).includes(role)
}

/**
 * 文档控制面镜像：与后端 `app/rag/access.py::can_control_document` 逐条对齐
 * （系统管理员 → 同租户租户管理员 → 本人上传且为当前版）。
 *
 * **只用于决定「要不要展示下载源文件入口」**。后端
 * `GET /rag/documents/{id}/download` 自己还要过一遍 `can_control_document`，
 * 所以这里的显隐既不放宽也不收紧后端判定：看不见入口的人本来就下不下来，
 * 看得见入口的人本来就有权下载。
 */
export function canControlDocument(doc: DocumentOut, user: UserInfo | null): boolean {
  if (!user) return false
  if (user.role === 'system_admin') return true
  if (doc.tenant_id !== user.tenant_id) return false
  if (user.role === 'tenant_admin') return true
  return doc.user_id === user.id && doc.is_current
}

/** 审计日志入口：后端限定 system_admin / system_viewer。 */
export function canViewAudit(role: Role | undefined): boolean {
  return role === 'system_admin' || role === 'system_viewer'
}

/** 租户页与用户页：后端这两个接口只允许 system_admin。 */
export function canManageTenants(role: Role | undefined): boolean {
  return role === 'system_admin'
}
