/** 用户页：创建成员，并列出、改角色、停用。仅系统管理员可操作。 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { ShieldOff } from 'lucide-react'

import { useCreateMember, useTenants } from '@/api/tenants'
import { useChangeRole, useDisableUser, useUsers } from '@/api/users'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { Input, Select } from '@/components/ui/Field'
import { Modal } from '@/components/ui/Modal'
import { ApiError } from '@/lib/http'
import { canManageTenants } from '@/lib/permissions'
import { useAuthStore } from '@/store/auth'
import { ROLE_META, type Role, type UserAdminOut } from '@/types/api'

const ROLES = Object.keys(ROLE_META) as Role[]

export default function UsersPage() {
  const me = useAuthStore((s) => s.user)
  const allowed = canManageTenants(me?.role)
  const tenants = useTenants(false)
  const create = useCreateMember()
  const [page, setPage] = useState(1)
  const [username, setUsername] = useState('')
  const [applied, setApplied] = useState('')
  const users = useUsers({ page, page_size: 20, username: applied || undefined }, allowed)

  const [tenantId, setTenantId] = useState('')
  const [newName, setNewName] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [createError, setCreateError] = useState('')
  const [createdUsername, setCreatedUsername] = useState('')
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')
  const [pendingDisable, setPendingDisable] = useState<UserAdminOut | null>(null)

  const options = (tenants.data ?? []).filter((tenant) => tenant.is_active)
  const selected = tenantId || options[0]?.id || ''

  if (!allowed) {
    return (
      <div className="panel">
        <EmptyState
          icon={<ShieldOff className="size-5" aria-hidden />}
          title="当前角色无权管理用户"
          description="用户列表、改角色和停用只对平台管理员开放。"
        />
      </div>
    )
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault()
    setCreateError('')
    setCreatedUsername('')
    if (!selected) {
      setCreateError('请先创建租户')
      return
    }
    try {
      const user = await create.mutateAsync({
        tenantId: selected,
        username: newName.trim(),
        password,
        email: email.trim(),
      })
      setNewName('')
      setPassword('')
      setEmail('')
      setCreatedUsername(user.username)
      setNotice(`已创建 ${user.username}`)
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.detail : '创建失败')
    }
  }

  function onSearch(event: FormEvent) {
    event.preventDefault()
    setPage(1)
    setApplied(username.trim())
  }

  const total = users.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / 20))

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader title="用户" description="查看用户编号和所属租户，修改角色或停用。停用后该用户不能再访问。" />

      {tenants.isLoading ? (
        <SkeletonRows rows={2} />
      ) : tenants.isError ? (
        <ErrorState error={tenants.error} onRetry={() => void tenants.refetch()} />
      ) : (
        <form onSubmit={(event) => void onCreate(event)} className="panel space-y-4 p-4">
          <Select label="租户" value={selected} onChange={(event) => setTenantId(event.target.value)} required>
            {options.length === 0 && <option value="">还没有未停用的租户</option>}
            {options.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.name}
              </option>
            ))}
          </Select>
          <Input label="用户名" value={newName} onChange={(event) => setNewName(event.target.value)} required autoComplete="off" />
          <Input label="密码" type="password" value={password} onChange={(event) => setPassword(event.target.value)} required autoComplete="new-password" />
          <Input label="邮箱" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="off" />
          {createError && <p className="text-sm text-danger">{createError}</p>}
          {createdUsername && <p className="text-sm text-text-muted">已创建 {createdUsername}</p>}
          <Button type="submit" variant="primary" loading={create.isPending} disabled={!newName.trim() || !password}>
            创建成员
          </Button>
        </form>
      )}

      <form onSubmit={onSearch} className="flex items-end gap-3">
        <div className="min-w-0 flex-1">
          <Input label="按用户名筛选" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="off" />
        </div>
        <Button type="submit" variant="secondary">筛选</Button>
      </form>

      {notice && <p className="text-sm text-text-muted">{notice}</p>}
      {actionError && <p className="text-sm text-danger">{actionError}</p>}

      {users.isLoading ? (
        <SkeletonRows />
      ) : users.isError ? (
        <ErrorState error={users.error} onRetry={() => void users.refetch()} />
      ) : (users.data?.items.length ?? 0) === 0 ? (
        <div className="panel">
          <EmptyState title="没有匹配的用户" description="换一个用户名，或先在上面创建成员。" />
        </div>
      ) : (
        <ul className="panel divide-y divide-border">
          {users.data?.items.map((user) => (
            <UserRow
              key={user.id}
              user={user}
              self={user.id === me?.id}
              onDone={setNotice}
              onFail={setActionError}
              onDisable={setPendingDisable}
            />
          ))}
        </ul>
      )}

      {total > 20 && (
        <div className="flex items-center justify-between text-sm text-text-muted">
          <span>第 {page} / {pageCount} 页</span>
          <div className="flex gap-2">
            <Button type="button" variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage((n) => n - 1)}>
              上一页
            </Button>
            <Button type="button" variant="secondary" size="sm" disabled={page >= pageCount} onClick={() => setPage((n) => n + 1)}>
              下一页
            </Button>
          </div>
        </div>
      )}

      <Modal
        open={pendingDisable !== null}
        onClose={() => setPendingDisable(null)}
        title="停用用户"
        description={
          pendingDisable
            ? `停用 ${pendingDisable.username} 后，该用户的令牌会失效，不能再访问。`
            : undefined
        }
        footer={
          <>
            <Button type="button" variant="secondary" onClick={() => setPendingDisable(null)}>
              取消
            </Button>
            <DisableButton
              user={pendingDisable}
              onClose={() => setPendingDisable(null)}
              onDone={setNotice}
              onFail={setActionError}
            />
          </>
        }
      >
        <p className="text-sm text-text-muted">这不会删除账号，只是停止登录和继续访问。</p>
      </Modal>
    </div>
  )
}

function UserRow({
  user,
  self,
  onDone,
  onFail,
  onDisable,
}: {
  user: UserAdminOut
  self: boolean
  onDone: (message: string) => void
  onFail: (message: string) => void
  onDisable: (user: UserAdminOut) => void
}) {
  const change = useChangeRole()

  async function onRole(role: Role) {
    onFail('')
    if (role === user.role) return
    try {
      await change.mutateAsync({ userId: user.id, role })
      onDone(`已将 ${user.username} 的角色改为${ROLE_META[role].label}`)
    } catch (err) {
      onFail(err instanceof ApiError ? err.detail : '修改角色失败')
    }
  }

  return (
    <li className="space-y-3 px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm text-text">
          {user.username}
          <span className="ml-2 text-text-muted">{user.is_active ? ROLE_META[user.role].label : '已停用'}</span>
        </p>
        <p className="font-mono text-xs text-text-faint">{user.id}</p>
      </div>
      <p className="text-xs text-text-muted">
        租户 {user.tenant_name}
        <span className="ml-2 font-mono text-text-faint">{user.tenant_id}</span>
      </p>
      {!self && user.is_active && (
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-40 flex-1">
            <Select
              label="角色"
              value={user.role}
              onChange={(event) => void onRole(event.target.value as Role)}
              disabled={change.isPending}
            >
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {ROLE_META[role].label}
                </option>
              ))}
            </Select>
          </div>
          <Button type="button" variant="danger" onClick={() => onDisable(user)}>
            停用
          </Button>
        </div>
      )}
    </li>
  )
}

function DisableButton({
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
  return (
    <Button
      type="button"
      variant="danger"
      loading={disable.isPending}
      disabled={!user}
      onClick={() => {
        if (!user) return
        void disable.mutateAsync(user.id).then(
          () => {
            onFail('')
            onDone(`已停用 ${user.username}`)
            onClose()
          },
          (err: unknown) => {
            onFail(err instanceof ApiError ? err.detail : '停用失败')
            onClose()
          },
        )
      }}
    >
      确认停用
    </Button>
  )
}
