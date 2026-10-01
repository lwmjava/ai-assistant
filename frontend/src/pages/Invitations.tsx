/** 邀请码：管理员生成，已登录用户接受。接受后不自动切换租户。 */

import { useState } from 'react'
import type { FormEvent } from 'react'

import { useAcceptInvitation, useCreateInvitation, useInvitations, useMemberships } from '@/api/invitations'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Field'
import { ApiError } from '@/lib/http'
import { useAuthStore } from '@/store/auth'

export default function InvitationsPage() {
  const user = useAuthStore((s) => s.user)
  const memberships = useMemberships()
  const accept = useAcceptInvitation()
  const create = useCreateInvitation()

  const current = (memberships.data ?? []).find((row) => row.tenant_id === user?.tenant_id)
  const canIssue = user?.role === 'system_admin' || current?.role === 'tenant_admin'
  const [tenantId, setTenantId] = useState(user?.role === 'system_admin' ? '' : (user?.tenant_id ?? ''))
  const listTenantId = user?.role === 'system_admin' ? tenantId.trim() || null : (user?.tenant_id ?? null)
  const invitations = useInvitations(canIssue ? listTenantId : null)

  const [code, setCode] = useState('')
  const [acceptError, setAcceptError] = useState('')
  const [joined, setJoined] = useState(false)
  const [issueError, setIssueError] = useState('')

  async function onAccept(event: FormEvent) {
    event.preventDefault()
    setAcceptError('')
    setJoined(false)
    try {
      await accept.mutateAsync(code.trim())
      setCode('')
      setJoined(true)
    } catch (err) {
      setAcceptError(err instanceof ApiError ? err.detail : '加入失败')
    }
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault()
    setIssueError('')
    try {
      await create.mutateAsync(user?.role === 'system_admin' ? tenantId.trim() : null)
    } catch (err) {
      setIssueError(err instanceof ApiError ? err.detail : '生成失败')
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader title="邀请" description="凭邀请码加入其他租户。加入后仍停留在当前租户，可从顶栏切换。" />

      <form onSubmit={(event) => void onAccept(event)} className="panel space-y-4 p-4">
        <Input
          label="邀请码"
          value={code}
          onChange={(event) => setCode(event.target.value)}
          required
          autoComplete="off"
        />
        {acceptError && <p className="text-sm text-danger">{acceptError}</p>}
        {joined && <p className="text-sm text-text-muted">已加入该租户。当前会话没有切换。</p>}
        <Button type="submit" variant="primary" loading={accept.isPending} disabled={!code.trim()}>
          加入租户
        </Button>
      </form>

      {canIssue && (
        <section className="panel space-y-4 p-4">
          <h2 className="text-sm font-medium text-text">生成邀请码</h2>
          <form onSubmit={(event) => void onCreate(event)} className="space-y-4">
            {user?.role === 'system_admin' && (
              <Input
                label="租户编号"
                value={tenantId}
                onChange={(event) => setTenantId(event.target.value)}
                required
                autoComplete="off"
              />
            )}
            {issueError && <p className="text-sm text-danger">{issueError}</p>}
            <Button
              type="submit"
              variant="primary"
              loading={create.isPending}
              disabled={user?.role === 'system_admin' && !tenantId.trim()}
            >
              生成邀请码
            </Button>
          </form>

          {invitations.isLoading ? (
            <p className="text-sm text-text-muted">正在加载邀请码</p>
          ) : invitations.isError ? (
            <p className="text-sm text-danger">
              {invitations.error instanceof ApiError ? invitations.error.detail : '无法加载邀请码'}
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {(invitations.data ?? []).map((row) => (
                <li key={row.id} className="py-3 text-sm">
                  <p className="font-mono text-text">{row.code}</p>
                  <p className="text-text-muted">
                    已用 {row.use_count}/{row.max_uses} · 到期 {row.expires_at.replace('T', ' ').slice(0, 16)}
                  </p>
                </li>
              ))}
              {(invitations.data ?? []).length === 0 && (
                <li className="py-3 text-sm text-text-muted">还没有邀请码</li>
              )}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}
