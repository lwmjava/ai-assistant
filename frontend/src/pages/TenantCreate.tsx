/** 新增租户。创建成功后回到列表。 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ShieldOff } from 'lucide-react'

import { useCreateTenant } from '@/api/tenants'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/Feedback'
import { Input } from '@/components/ui/Field'
import { ApiError } from '@/lib/http'
import { canManageTenants } from '@/lib/permissions'
import { useAuthStore } from '@/store/auth'

export default function TenantCreatePage() {
  const role = useAuthStore((s) => s.user?.role)
  const allowed = canManageTenants(role)
  const navigate = useNavigate()
  const create = useCreateTenant()
  const [name, setName] = useState('')
  const [error, setError] = useState('')

  if (!allowed) {
    return (
      <div className="panel">
        <EmptyState
          icon={<ShieldOff className="size-5" aria-hidden />}
          title="当前角色无权管理租户"
          description="创建租户只对平台管理员开放。"
        />
      </div>
    )
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError('')
    try {
      const tenant = await create.mutateAsync(name.trim())
      navigate('/tenants', { state: { notice: `已创建 ${tenant.name}` } })
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : '创建失败')
    }
  }

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <PageHeader title="新增租户" description="创建后出现在租户列表中。未停用租户的名称不能重复。" />
      <form onSubmit={(event) => void onSubmit(event)} className="panel space-y-4 p-4">
        <Input
          label="租户名称"
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          autoComplete="off"
        />
        {error && <p className="text-sm text-danger">{error}</p>}
        <div className="flex gap-2">
          <Button type="button" variant="secondary" onClick={() => navigate('/tenants')}>
            返回
          </Button>
          <Button type="submit" variant="primary" loading={create.isPending} disabled={!name.trim()}>
            创建
          </Button>
        </div>
      </form>
    </div>
  )
}
