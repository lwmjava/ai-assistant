/** 用户页：选择未停用租户并创建成员。仅系统管理员可进入。 */

import { useState } from 'react'
import type { FormEvent } from 'react'

import { useCreateMember, useTenants } from '@/api/tenants'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input, Select } from '@/components/ui/Field'
import { ApiError } from '@/lib/http'

export default function UsersPage() {
  const tenants = useTenants()
  const create = useCreateMember()
  const [tenantId, setTenantId] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [createdUsername, setCreatedUsername] = useState('')

  const options = tenants.data ?? []
  const selected = tenantId || options[0]?.id || ''

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setCreatedUsername('')
    if (!selected) {
      setError('请先创建租户')
      return
    }
    try {
      const user = await create.mutateAsync({
        tenantId: selected,
        username: username.trim(),
        password,
        email: email.trim(),
      })
      setUsername('')
      setPassword('')
      setEmail('')
      setCreatedUsername(user.username)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : '创建失败')
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="用户" description="在选定租户下创建成员。成员登录后只在该租户。" />

      {tenants.isLoading ? (
        <p className="text-sm text-text-muted">正在加载租户</p>
      ) : tenants.isError ? (
        <p className="text-sm text-danger">
          {tenants.error instanceof ApiError ? tenants.error.detail : '无法加载租户'}
        </p>
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
            minLength={8}
            autoComplete="new-password"
          />
          <Input
            label="邮箱"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="off"
          />
          {error && <p className="text-sm text-danger">{error}</p>}
          {createdUsername && <p className="text-sm text-text-muted">已创建成员 {createdUsername}</p>}
          <Button
            type="submit"
            variant="primary"
            loading={create.isPending}
            disabled={!selected || !username.trim() || password.length < 8}
          >
            创建成员
          </Button>
        </form>
      )}
    </div>
  )
}
