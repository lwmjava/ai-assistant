/** 用户列表：筛选、分页、新增页入口，行末操作含详情、修改和停用/启用。 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ShieldOff } from 'lucide-react'

import { useTenants } from '@/api/tenants'
import { useChangeRole, useDisableUser, useEnableUser, useUsers } from '@/api/users'
import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState } from '@/components/ui/Feedback'
import { Input, Select } from '@/components/ui/Field'
import { Modal } from '@/components/ui/Modal'
import { Pager } from '@/components/ui/Pager'
import { ApiError } from '@/lib/http'
import { canManageTenants } from '@/lib/permissions'
import { useAuthStore } from '@/store/auth'
import { ROLE_META, type Role, type UserAdminOut } from '@/types/api'

const PAGE_SIZE = 20
const ROLES = Object.keys(ROLE_META) as Role[]

interface UserFilters {
  username: string
  role: '' | Role
  active: '' | 'true' | 'false'
  tenantId: string
}

const EMPTY_FILTERS: UserFilters = { username: '', role: '', active: '', tenantId: '' }

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.detail) return err.detail
  return fallback
}

function toQuery(filters: UserFilters, page: number) {
  return {
    page,
    page_size: PAGE_SIZE,
    username: filters.username.trim() || undefined,
    role: filters.role || undefined,
    is_active: filters.active === '' ? undefined : filters.active === 'true',
    tenant_id: filters.tenantId || undefined,
  }
}

export default function UsersPage() {
  const me = useAuthStore((s) => s.user)
  const allowed = canManageTenants(me?.role)
  const navigate = useNavigate()
  const location = useLocation()
  const tenants = useTenants(true, allowed)
  const [draftFilters, setDraftFilters] = useState<UserFilters>(EMPTY_FILTERS)
  const [filters, setFilters] = useState<UserFilters>(EMPTY_FILTERS)
  const [page, setPage] = useState(1)
  const users = useUsers(toQuery(filters, page), allowed)
  const [notice, setNotice] = useState(
    () => (location.state as { notice?: string } | null)?.notice ?? '',
  )
  const [actionError, setActionError] = useState('')
  const [detail, setDetail] = useState<UserAdminOut | null>(null)
  const [editor, setEditor] = useState<UserAdminOut | null>(null)
  const [pending, setPending] = useState<UserAdminOut | null>(null)

  const total = users.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  if (!allowed) {
    return (
      <div className="panel">
        <EmptyState
          icon={<ShieldOff className="size-5" aria-hidden />}
          title="当前角色无权管理用户"
          description="用户列表只对平台管理员开放。"
        />
      </div>
    )
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault()
    setPage(1)
    setFilters({ ...draftFilters })
  }

  const rows = users.data?.items ?? []

  return (
    <div className="space-y-4">
      <PageHeader
        title="用户"
        description="按用户名、角色、状态和租户查看用户。新增在表格上方，每条记录的操作在最后一列。"
      />

      <form className="flex flex-wrap items-end gap-3" onSubmit={applyFilters}>
        <Input
          label="用户名"
          value={draftFilters.username}
          onChange={(event) => setDraftFilters((prev) => ({ ...prev, username: event.target.value }))}
          placeholder="搜索用户名"
          wrapClassName="w-48"
          autoComplete="off"
        />
        <Select
          label="角色"
          value={draftFilters.role}
          onChange={(event) =>
            setDraftFilters((prev) => ({ ...prev, role: event.target.value as UserFilters['role'] }))
          }
          wrapClassName="w-40"
        >
          <option value="">全部</option>
          {ROLES.map((role) => (
            <option key={role} value={role}>
              {ROLE_META[role].label}
            </option>
          ))}
        </Select>
        <Select
          label="状态"
          value={draftFilters.active}
          onChange={(event) =>
            setDraftFilters((prev) => ({
              ...prev,
              active: event.target.value as UserFilters['active'],
            }))
          }
          wrapClassName="w-32"
        >
          <option value="">全部</option>
          <option value="true">启用</option>
          <option value="false">停用</option>
        </Select>
        <Select
          label="租户"
          value={draftFilters.tenantId}
          onChange={(event) => setDraftFilters((prev) => ({ ...prev, tenantId: event.target.value }))}
          wrapClassName="w-48"
        >
          <option value="">全部租户</option>
          {(tenants.data ?? []).map((tenant) => (
            <option key={tenant.id} value={tenant.id}>
              {tenant.name}
              {tenant.is_active ? '' : '（已停用）'}
            </option>
          ))}
        </Select>
        <Button type="submit" variant="secondary">
          筛选
        </Button>
      </form>

      {notice && <p className="text-sm text-text-muted">{notice}</p>}
      {actionError && <p className="text-sm text-danger">{actionError}</p>}

      <div>
        <Button type="button" variant="primary" onClick={() => navigate('/users/new')}>
          新增用户
        </Button>
      </div>

      <div className="panel overflow-x-auto">
        {users.isLoading && <p className="px-4 py-8 text-sm text-text-muted">正在加载用户…</p>}
        {users.isError && <ErrorState error={users.error} onRetry={() => void users.refetch()} />}
        {users.isSuccess && rows.length === 0 && (
          <EmptyState title="没有符合条件的用户" description="调整筛选条件，或新增一个用户。" />
        )}
        {users.isSuccess && rows.length > 0 && (
          <table className="w-full min-w-[880px] text-left text-sm">
            <thead className="border-b border-border bg-surface-2/70 text-text-muted">
              <tr>
                <th className="px-4 py-3 font-medium">用户名</th>
                <th className="px-4 py-3 font-medium">角色</th>
                <th className="px-4 py-3 font-medium">租户</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((user) => {
                const self = user.id === me?.id
                return (
                  <tr key={user.id} className="border-b border-border/70 last:border-0">
                    <td className="px-4 py-3 font-medium text-text">{user.username}</td>
                    <td className="px-4 py-3 text-text-muted">{ROLE_META[user.role].label}</td>
                    <td className="px-4 py-3 text-text-muted">{user.tenant_name}</td>
                    <td className="px-4 py-3">
                      <Badge tone={user.is_active ? 'success' : 'warning'}>
                        {user.is_active ? '启用' : '停用'}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-1 whitespace-nowrap">
                        <Button size="sm" variant="ghost" onClick={() => setDetail(user)}>
                          详情
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={self}
                          title={self ? '不能修改自己的角色' : undefined}
                          onClick={() => setEditor(user)}
                        >
                          修改
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={self && user.is_active}
                          title={self && user.is_active ? '不能停用自己' : undefined}
                          onClick={() => setPending(user)}
                        >
                          {user.is_active ? '停用' : '启用'}
                        </Button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {users.isSuccess && <Pager page={page} pageCount={pageCount} total={total} onPage={setPage} />}

      <UserDetailModal user={detail} onClose={() => setDetail(null)} />
      {editor && (
        <RoleModal
          key={editor.id}
          user={editor}
          onClose={() => setEditor(null)}
          onDone={setNotice}
          onFail={setActionError}
        />
      )}
      <ToggleUserModal
        user={pending}
        onClose={() => setPending(null)}
        onDone={(message) => {
          setActionError('')
          setNotice(message)
        }}
        onFail={setActionError}
      />
    </div>
  )
}

function UserDetailModal({ user, onClose }: { user: UserAdminOut | null; onClose: () => void }) {
  return (
    <Modal open={user !== null} onClose={onClose} title="用户详情">
      {user && (
        <div className="space-y-3 text-sm">
          <p>
            <span className="text-text-muted">用户名：</span>
            {user.username}
          </p>
          <p>
            <span className="text-text-muted">编号：</span>
            <span className="font-mono text-xs">{user.id}</span>
          </p>
          <p>
            <span className="text-text-muted">邮箱：</span>
            {user.email || '无'}
          </p>
          <p>
            <span className="text-text-muted">角色：</span>
            {ROLE_META[user.role].label}
          </p>
          <p>
            <span className="text-text-muted">租户：</span>
            {user.tenant_name}
            <span className="ml-2 font-mono text-xs text-text-faint">{user.tenant_id}</span>
          </p>
          <p>
            <span className="text-text-muted">状态：</span>
            {user.is_active ? '启用' : '停用'}
          </p>
        </div>
      )}
    </Modal>
  )
}

function RoleModal({
  user,
  onClose,
  onDone,
  onFail,
}: {
  user: UserAdminOut
  onClose: () => void
  onDone: (message: string) => void
  onFail: (message: string) => void
}) {
  const change = useChangeRole()
  const [role, setRole] = useState<Role>(user.role)
  const [error, setError] = useState('')

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError('')
    try {
      await change.mutateAsync({ userId: user.id, role })
      onFail('')
      onDone(`已将 ${user.username} 的角色改为${ROLE_META[role].label}`)
      onClose()
    } catch (err) {
      const message = errorText(err, '修改角色失败')
      setError(message)
      onFail(message)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="修改用户"
      footer={
        <>
          <Button type="button" variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" type="submit" form="user-role" loading={change.isPending}>
            保存
          </Button>
        </>
      }
    >
      <form id="user-role" className="space-y-3" onSubmit={(event) => void onSubmit(event)}>
        <Select label="角色" value={role} onChange={(event) => setRole(event.target.value as Role)}>
          {ROLES.map((item) => (
            <option key={item} value={item}>
              {ROLE_META[item].label}
            </option>
          ))}
        </Select>
        {error && <p className="text-sm text-danger">{error}</p>}
      </form>
    </Modal>
  )
}

function ToggleUserModal({
  user,
  onClose,
  onDone,
  onFail,
}: {
  user: UserAdminOut | null
  onClose: () => void
  onDone: (message: string) => void
  onFail: (message: string) => void
}) {
  const disable = useDisableUser()
  const enable = useEnableUser()
  const pending = disable.isPending || enable.isPending
  const enabling = user ? !user.is_active : false

  return (
    <Modal
      open={user !== null}
      onClose={onClose}
      title={enabling ? '启用用户' : '停用用户'}
      description={
        user
          ? enabling
            ? `启用 ${user.username} 后，该用户可以重新登录。之前发出的令牌仍然无效。`
            : `停用 ${user.username} 后，该用户的令牌会失效，不能再访问。`
          : undefined
      }
      footer={
        <>
          <Button type="button" variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            type="button"
            variant={enabling ? 'primary' : 'danger'}
            loading={pending}
            disabled={!user}
            onClick={() => {
              if (!user) return
              const action = enabling ? enable.mutateAsync(user.id) : disable.mutateAsync(user.id)
              void action.then(
                () => {
                  onDone(enabling ? `已启用 ${user.username}` : `已停用 ${user.username}`)
                  onClose()
                },
                (err: unknown) => {
                  onFail(errorText(err, enabling ? '启用失败' : '停用失败'))
                  onClose()
                },
              )
            }}
          >
            {enabling ? '确认启用' : '确认停用'}
          </Button>
        </>
      }
    >
      <p className="text-sm text-text-muted">这不会删除账号。</p>
    </Modal>
  )
}
