/** 租户页：创建、改名、停用，并显示编号。仅系统管理员可操作。 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { ShieldOff } from 'lucide-react'

import { useCreateTenant, useDeactivateTenant, useRenameTenant, useTenants, type TenantOut } from '@/api/tenants'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { Input } from '@/components/ui/Field'
import { Modal } from '@/components/ui/Modal'
import { ApiError } from '@/lib/http'
import { canManageTenants } from '@/lib/permissions'
import { useAuthStore } from '@/store/auth'

export default function TenantsPage() {
  const role = useAuthStore((s) => s.user?.role)
  const allowed = canManageTenants(role)
  const tenants = useTenants(true)
  const create = useCreateTenant()
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pending, setPending] = useState<TenantOut | null>(null)

  if (!allowed) {
    return (
      <div className="panel">
        <EmptyState
          icon={<ShieldOff className="size-5" aria-hidden />}
          title="当前角色无权管理租户"
          description="创建、改名和停用租户只对平台管理员开放。"
        />
      </div>
    )
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError('')
    try {
      const tenant = await create.mutateAsync(name.trim())
      setName('')
      setNotice(`已创建 ${tenant.name}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : '创建失败')
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title="租户"
        description="创建或修改租户。编号可复制到邀请页。停用后，当前正在该租户中的成员不能继续访问。"
      />

      <form onSubmit={(event) => void onSubmit(event)} className="panel space-y-4 p-4">
        <Input label="租户名称" value={name} onChange={(event) => setName(event.target.value)} required autoComplete="off" />
        {error && <p className="text-sm text-danger">{error}</p>}
        {notice && <p className="text-sm text-text-muted">{notice}</p>}
        <Button type="submit" variant="primary" loading={create.isPending} disabled={!name.trim()}>
          创建租户
        </Button>
      </form>

      {tenants.isLoading ? (
        <SkeletonRows />
      ) : tenants.isError ? (
        <ErrorState error={tenants.error} onRetry={() => void tenants.refetch()} />
      ) : (tenants.data?.length ?? 0) === 0 ? (
        <div className="panel">
          <EmptyState title="还没有租户" description="先在上面创建一个租户。" />
        </div>
      ) : (
        <ul className="panel divide-y divide-border">
          {tenants.data?.map((tenant) => (
            <TenantRow key={tenant.id} tenant={tenant} onDone={setNotice} onFail={setError} onDeactivate={setPending} />
          ))}
        </ul>
      )}

      <Modal
        open={pending !== null}
        onClose={() => setPending(null)}
        title="停用租户"
        description={pending ? `停用「${pending.name}」后，当前正在该租户中的成员将不能继续访问。` : undefined}
        footer={
          <>
            <Button type="button" variant="secondary" onClick={() => setPending(null)}>取消</Button>
            <DeactivateButton tenant={pending} onClose={() => setPending(null)} onDone={setNotice} onFail={setError} />
          </>
        }
      >
        <p className="text-sm text-text-muted">租户不会被删除，编号仍然可以在列表里看到。</p>
      </Modal>
    </div>
  )
}

function TenantRow({
  tenant,
  onDone,
  onFail,
  onDeactivate,
}: {
  tenant: TenantOut
  onDone: (message: string) => void
  onFail: (message: string) => void
  onDeactivate: (tenant: TenantOut) => void
}) {
  const rename = useRenameTenant()
  const [name, setName] = useState(tenant.name)

  async function onRename(event: FormEvent) {
    event.preventDefault()
    onFail('')
    try {
      const updated = await rename.mutateAsync({ tenantId: tenant.id, name: name.trim() })
      onDone(`已将租户改名为 ${updated.name}`)
    } catch (err) {
      onFail(err instanceof ApiError ? err.detail : '改名失败')
    }
  }

  return (
    <li className="space-y-3 px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm text-text">
          {tenant.name}
          {!tenant.is_active && <span className="ml-2 text-text-muted">已停用</span>}
        </p>
        <p className="font-mono text-xs text-text-faint">{tenant.id}</p>
      </div>
      {tenant.is_active && (
        <form onSubmit={(event) => void onRename(event)} className="flex flex-wrap items-end gap-3">
          <div className="min-w-40 flex-1">
            <Input label="新名称" value={name} onChange={(event) => setName(event.target.value)} required />
          </div>
          <Button type="submit" variant="secondary" loading={rename.isPending} disabled={!name.trim()}>
            保存名称
          </Button>
          <Button type="button" variant="danger" onClick={() => onDeactivate(tenant)}>
            停用
          </Button>
        </form>
      )}
    </li>
  )
}

function DeactivateButton({
  tenant,
  onClose,
  onDone,
  onFail,
}: {
  tenant: TenantOut | null
  onClose: () => void
  onDone: (message: string) => void
  onFail: (message: string) => void
}) {
  const deactivate = useDeactivateTenant()
  return (
    <Button
      type="button"
      variant="danger"
      loading={deactivate.isPending}
      disabled={!tenant}
      onClick={() => {
        if (!tenant) return
        void deactivate.mutateAsync(tenant.id).then(
          () => {
            onFail('')
            onDone(`已停用 ${tenant.name}`)
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
