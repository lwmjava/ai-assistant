/** 租户设置页（ADM-04）：管理本租户成员，并入口到知识库。 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { BookOpen, UserPlus } from 'lucide-react'

import { useCreateTenantMember, useTenantMembers } from '@/api/tenant-settings'
import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { Input } from '@/components/ui/Field'
import { ForbiddenPage } from '@/pages/Forbidden'
import { ApiError } from '@/lib/http'
import { can } from '@/lib/permissions'
import { useAuthStore } from '@/store/auth'
import { ROLE_META } from '@/types/api'

export default function TenantSettingsPage() {
  const role = useAuthStore((s) => s.user?.role)
  const allowed = can(role, 'members', 'write')
  const canReadKb = can(role, 'knowledge_bases', 'read')
  const members = useTenantMembers(allowed)
  const createMember = useCreateTenantMember()
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')

  if (!allowed) {
    return (
      <ForbiddenPage
        title="当前角色无权打开租户设置"
        description="本租户成员与知识库管理仅对租户管理员开放。"
      />
    )
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault()
    setFormError('')
    setNotice('')
    try {
      await createMember.mutateAsync({
        username: username.trim(),
        password,
        email: email.trim() || null,
      })
      setUsername('')
      setEmail('')
      setPassword('')
      setNotice('已创建成员')
    } catch (err) {
      setFormError(err instanceof ApiError ? err.detail : '创建失败')
    }
  }

  const rows = members.data ?? []

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title="租户设置"
        description="管理本租户成员，并通过知识库页维护本租户文档。"
      />

      {canReadKb && (
        <div className="panel flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-2 text-sm text-text">
            <BookOpen className="size-4 text-primary" aria-hidden />
            <span>本租户知识库可在「知识库」页上传、检索与管理文档。</span>
          </div>
          <Link
            to="/knowledge"
            className="inline-flex h-10 items-center rounded-lg bg-primary/12 px-3 text-sm font-medium text-primary ring-1 ring-primary/25"
          >
            打开知识库
          </Link>
        </div>
      )}

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-text">本租户成员</h2>
        {members.isLoading ? (
          <SkeletonRows rows={3} />
        ) : members.isError ? (
          <ErrorState error={members.error} onRetry={() => void members.refetch()} />
        ) : rows.length === 0 ? (
          <div className="panel">
            <EmptyState title="暂无成员" description="可用下方表单添加成员。" />
          </div>
        ) : (
          <ul className="panel divide-y divide-border">
            {rows.map((user) => (
              <li key={user.id} className="flex items-center justify-between gap-3 px-4 py-3 text-sm">
                <div className="min-w-0">
                  <p className="truncate font-medium text-text">{user.username}</p>
                  <p className="truncate text-text-muted">{user.email || '无邮箱'}</p>
                </div>
                <Badge tone={ROLE_META[user.role]?.tone ?? 'neutral'}>
                  {ROLE_META[user.role]?.label ?? user.role}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel space-y-4 p-4">
        <h2 className="flex items-center gap-2 text-sm font-medium text-text">
          <UserPlus className="size-4" aria-hidden />
          添加成员
        </h2>
        <form className="grid gap-3 sm:grid-cols-2" onSubmit={onCreate}>
          <Input
            label="用户名"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="off"
          />
          <Input
            label="邮箱"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="off"
            placeholder="选填"
          />
          <Input
            label="初始密码"
            type="password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="new-password"
            wrapClassName="sm:col-span-2"
          />
          {formError && (
            <p role="alert" className="sm:col-span-2 text-sm text-danger">
              {formError}
            </p>
          )}
          {notice && (
            <p role="status" className="sm:col-span-2 text-sm text-primary">
              {notice}
            </p>
          )}
          <div className="sm:col-span-2">
            <Button type="submit" variant="primary" loading={createMember.isPending}>
              创建成员
            </Button>
          </div>
        </form>
      </section>
    </div>
  )
}
