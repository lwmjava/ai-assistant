/** 租户页：列出未停用租户并创建新租户。仅系统管理员可进入。 */

import { useState } from 'react'
import type { FormEvent } from 'react'

import { useCreateTenant, useTenants } from '@/api/tenants'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Field'
import { ApiError } from '@/lib/http'

export default function TenantsPage() {
  const tenants = useTenants()
  const create = useCreateTenant()
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [createdName, setCreatedName] = useState('')

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setCreatedName('')
    try {
      const tenant = await create.mutateAsync(name.trim())
      setName('')
      setCreatedName(tenant.name)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : '创建失败')
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="租户" description="创建租户，并查看尚未停用的租户。" />

      <form onSubmit={(event) => void onSubmit(event)} className="panel mb-6 space-y-4 p-4">
        <Input
          label="租户名称"
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          autoComplete="off"
        />
        {error && <p className="text-sm text-danger">{error}</p>}
        {createdName && <p className="text-sm text-text-muted">已创建 {createdName}</p>}
        <Button type="submit" variant="primary" loading={create.isPending} disabled={!name.trim()}>
          创建租户
        </Button>
      </form>

      {tenants.isLoading ? (
        <p className="text-sm text-text-muted">正在加载租户</p>
      ) : tenants.isError ? (
        <p className="text-sm text-danger">
          {tenants.error instanceof ApiError ? tenants.error.detail : '无法加载租户'}
        </p>
      ) : (
        <ul className="panel divide-y divide-border">
          {(tenants.data ?? []).map((tenant) => (
            <li key={tenant.id} className="px-4 py-3 text-sm text-text">
              {tenant.name}
            </li>
          ))}
          {(tenants.data ?? []).length === 0 && (
            <li className="px-4 py-3 text-sm text-text-muted">还没有未停用的租户</li>
          )}
        </ul>
      )}
    </div>
  )
}
