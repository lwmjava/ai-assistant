/** 新增用户。创建成功后回到列表。角色固定为成员。 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ShieldOff } from 'lucide-react'

import { useCreateMember, useTenants } from '@/api/tenants'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { Input, Select } from '@/components/ui/Field'
import { ApiError } from '@/lib/http'
import { canManageTenants } from '@/lib/permissions'
import { useAuthStore } from '@/store/auth'

export default function UserCreatePage() {
  const role = useAuthStore((s) => s.user?.role)
  const allowed = canManageTenants(role)
  const navigate = useNavigate()
  const tenants = useTenants(false, allowed)
  const create = useCreateMember()
  const [tenantId, setTenantId] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')

  const options = (tenants.data ?? []).filter((tenant) => tenant.is_active)
  const selected = tenantId || options[0]?.id || ''

  if (!allowed) {
    return (
      <div className="panel">
        <EmptyState
          icon={<ShieldOff className="size-5" aria-hidden />}
          title="当前角色无权管理用户"
          description="创建用户只对平台管理员开放。"
        />
      </div>
    )
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError('')
    if (!selected) {
      setError('请先创建未停用的租户')
      return
    }
    try {
      const user = await create.mutateAsync({
        tenantId: selected,
        username: username.trim(),
        password,
        email: email.trim(),
      })
      navigate('/users', { state: { notice: `已创建 ${user.username}` } })
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : '创建失败')
    }
  }

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <PageHeader title="新增用户" description="在未停用的租户下创建成员。角色固定为成员，密码至少 8 位。" />
      {tenants.isLoading ? (
        <SkeletonRows rows={3} />
      ) : tenants.isError ? (
        <ErrorState error={tenants.error} onRetry={() => void tenants.refetch()} />
      ) : (
        <form onSubmit={(event) => void onSubmit(event)} className="panel space-y-4 p-4">
          <Select
            label="租户"
            value={selected}
            onChange={(event) => setTenantId(event.target.value)}
            required
          >
            {options.length === 0 && <option value="">还没有未停用的租户</option>}
            {options.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.name}
              </option>
            ))}
          </Select>
          <Input
            label="用户名"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
            autoComplete="off"
          />
          <Input
            label="密码"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            autoComplete="new-password"
          />
          <Input
            label="邮箱"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="off"
          />
          {error && <p className="text-sm text-danger">{error}</p>}
          <div className="flex gap-2">
            <Button type="button" variant="secondary" onClick={() => navigate('/users')}>
              返回
            </Button>
            <Button
              type="submit"
              variant="primary"
              loading={create.isPending}
              disabled={!username.trim() || !password || !selected}
            >
              创建
            </Button>
          </div>
        </form>
      )}
    </div>
  )
}
