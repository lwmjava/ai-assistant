/** 租户列表：筛选、分页、新增页入口，行末操作含详情、修改、配额和停用/启用。 */

import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ShieldOff } from 'lucide-react'

import {
  useActivateTenant,
  useDeactivateTenant,
  useRenameTenant,
  useTenantQuota,
  useTenants,
  useUpdateTenantQuota,
  type TenantOut,
} from '@/api/tenants'
import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState } from '@/components/ui/Feedback'
import { Input, Select, Textarea } from '@/components/ui/Field'
import { Modal } from '@/components/ui/Modal'
import { Pager } from '@/components/ui/Pager'
import { ApiError } from '@/lib/http'
import { canManageTenants } from '@/lib/permissions'
import { useAuthStore } from '@/store/auth'

const PAGE_SIZE = 20

interface TenantFilters {
  q: string
  active: '' | 'true' | 'false'
}

const EMPTY_FILTERS: TenantFilters = { q: '', active: '' }

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.detail) return err.detail
  return fallback
}

function parseLimit(raw: string): number | null | 'invalid' {
  const text = raw.trim()
  if (!text) return null
  if (!/^\d+$/.test(text)) return 'invalid'
  const value = Number(text)
  if (!Number.isSafeInteger(value)) return 'invalid'
  return value
}

function formatLimit(value: number | null | undefined, unit: string): string {
  if (value == null) return '不限制'
  if (value === 0) return `0${unit}（不能再新增）`
  return `${value}${unit}`
}

export default function TenantsPage() {
  const role = useAuthStore((s) => s.user?.role)
  const allowed = canManageTenants(role)
  const navigate = useNavigate()
  const location = useLocation()
  const tenants = useTenants(true, allowed)
  const [draftFilters, setDraftFilters] = useState<TenantFilters>(EMPTY_FILTERS)
  const [filters, setFilters] = useState<TenantFilters>(EMPTY_FILTERS)
  const [page, setPage] = useState(1)
  const [notice, setNotice] = useState(
    () => (location.state as { notice?: string } | null)?.notice ?? '',
  )
  const [actionError, setActionError] = useState('')
  const [detail, setDetail] = useState<TenantOut | null>(null)
  const [editor, setEditor] = useState<TenantOut | null>(null)
  const [quotaTarget, setQuotaTarget] = useState<TenantOut | null>(null)
  const [pending, setPending] = useState<TenantOut | null>(null)

  const filtered = useMemo(() => {
    const rows = tenants.data ?? []
    const needle = filters.q.trim().toLowerCase()
    return rows.filter((tenant) => {
      if (needle && !tenant.name.toLowerCase().includes(needle)) return false
      if (filters.active === 'true' && !tenant.is_active) return false
      if (filters.active === 'false' && tenant.is_active) return false
      return true
    })
  }, [tenants.data, filters])

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount)
  const rows = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)

  useEffect(() => {
    if (page !== safePage) setPage(safePage)
  }, [page, safePage])

  if (!allowed) {
    return (
      <div className="panel">
        <EmptyState
          icon={<ShieldOff className="size-5" aria-hidden />}
          title="当前角色无权管理租户"
          description="租户列表只对平台管理员开放。"
        />
      </div>
    )
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault()
    setPage(1)
    setFilters({ ...draftFilters })
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="租户"
        description="按名称和状态查看租户。新增在表格上方，每条记录的操作在最后一列。"
      />

      <form className="flex flex-wrap items-end gap-3" onSubmit={applyFilters}>
        <Input
          label="名称"
          value={draftFilters.q}
          onChange={(event) => setDraftFilters((prev) => ({ ...prev, q: event.target.value }))}
          placeholder="搜索租户名称"
          wrapClassName="w-56"
          autoComplete="off"
        />
        <Select
          label="状态"
          value={draftFilters.active}
          onChange={(event) =>
            setDraftFilters((prev) => ({
              ...prev,
              active: event.target.value as TenantFilters['active'],
            }))
          }
          wrapClassName="w-32"
        >
          <option value="">全部</option>
          <option value="true">启用</option>
          <option value="false">停用</option>
        </Select>
        <Button type="submit" variant="secondary">
          筛选
        </Button>
      </form>

      {notice && <p className="text-sm text-text-muted">{notice}</p>}
      {actionError && <p className="text-sm text-danger">{actionError}</p>}

      <div>
        <Button type="button" variant="primary" onClick={() => navigate('/tenants/new')}>
          新增租户
        </Button>
      </div>

      <div className="panel overflow-x-auto">
        {tenants.isLoading && <p className="px-4 py-8 text-sm text-text-muted">正在加载租户…</p>}
        {tenants.isError && <ErrorState error={tenants.error} onRetry={() => void tenants.refetch()} />}
        {tenants.isSuccess && rows.length === 0 && (
          <EmptyState title="没有符合条件的租户" description="调整筛选条件，或新增一个租户。" />
        )}
        {tenants.isSuccess && rows.length > 0 && (
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="border-b border-border bg-surface-2/70 text-text-muted">
              <tr>
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">编号</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((tenant) => (
                <tr key={tenant.id} className="border-b border-border/70 last:border-0">
                  <td className="px-4 py-3 font-medium text-text">{tenant.name}</td>
                  <td className="px-4 py-3 font-mono text-xs text-text-faint">{tenant.id}</td>
                  <td className="px-4 py-3">
                    <Badge tone={tenant.is_active ? 'success' : 'warning'}>
                      {tenant.is_active ? '启用' : '停用'}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1 whitespace-nowrap">
                      <Button size="sm" variant="ghost" onClick={() => setDetail(tenant)}>
                        详情
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditor(tenant)}>
                        修改
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setQuotaTarget(tenant)}>
                        配额
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setPending(tenant)}>
                        {tenant.is_active ? '停用' : '启用'}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {tenants.isSuccess && (
        <Pager page={safePage} pageCount={pageCount} total={filtered.length} onPage={setPage} />
      )}

      <TenantDetailModal tenant={detail} onClose={() => setDetail(null)} />
      {editor && (
        <RenameModal
          key={editor.id}
          tenant={editor}
          onClose={() => setEditor(null)}
          onDone={setNotice}
          onFail={setActionError}
        />
      )}
      {quotaTarget && (
        <QuotaModal
          key={quotaTarget.id}
          tenant={quotaTarget}
          onClose={() => setQuotaTarget(null)}
          onDone={setNotice}
          onFail={setActionError}
        />
      )}
      <ToggleModal
        tenant={pending}
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

function TenantDetailModal({ tenant, onClose }: { tenant: TenantOut | null; onClose: () => void }) {
  const quota = useTenantQuota(tenant?.id ?? null)
  return (
    <Modal open={tenant !== null} onClose={onClose} title="租户详情">
      {tenant && (
        <div className="space-y-3 text-sm">
          <p>
            <span className="text-text-muted">名称：</span>
            {tenant.name}
          </p>
          <p>
            <span className="text-text-muted">编号：</span>
            <span className="font-mono text-xs">{tenant.id}</span>
          </p>
          <p>
            <span className="text-text-muted">状态：</span>
            {tenant.is_active ? '启用' : '停用'}
          </p>
          {quota.isLoading && <p className="text-text-muted">正在读取上限…</p>}
          {quota.isError && <p className="text-danger">{errorText(quota.error, '上限读取失败')}</p>}
          {quota.data && (
            <>
              <p>
                <span className="text-text-muted">消息条数上限：</span>
                {formatLimit(quota.data.message_limit, ' 条')}
              </p>
              <p>
                <span className="text-text-muted">源文件上限：</span>
                {formatLimit(quota.data.storage_limit_bytes, ' 字节')}
              </p>
            </>
          )}
        </div>
      )}
    </Modal>
  )
}

function RenameModal({
  tenant,
  onClose,
  onDone,
  onFail,
}: {
  tenant: TenantOut
  onClose: () => void
  onDone: (message: string) => void
  onFail: (message: string) => void
}) {
  const rename = useRenameTenant()
  const [name, setName] = useState(tenant.name)
  const [error, setError] = useState('')

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError('')
    try {
      const updated = await rename.mutateAsync({ tenantId: tenant.id, name: name.trim() })
      onFail('')
      onDone(`已将租户改名为 ${updated.name}`)
      onClose()
    } catch (err) {
      const message = errorText(err, '改名失败')
      setError(message)
      onFail(message)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="修改租户"
      description={tenant.is_active ? undefined : '停用中的租户不能改名，请先启用。'}
      footer={
        <>
          <Button type="button" variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="tenant-rename"
            loading={rename.isPending}
            disabled={!tenant.is_active || !name.trim()}
          >
            保存
          </Button>
        </>
      }
    >
      <form id="tenant-rename" className="space-y-3" onSubmit={(event) => void onSubmit(event)}>
        <Input label="名称" value={name} onChange={(event) => setName(event.target.value)} required autoComplete="off" />
        {error && <p className="text-sm text-danger">{error}</p>}
      </form>
    </Modal>
  )
}

function QuotaModal({
  tenant,
  onClose,
  onDone,
  onFail,
}: {
  tenant: TenantOut
  onClose: () => void
  onDone: (message: string) => void
  onFail: (message: string) => void
}) {
  const quota = useTenantQuota(tenant.id)
  const update = useUpdateTenantQuota()
  const [messageLimit, setMessageLimit] = useState('')
  const [storageLimit, setStorageLimit] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const [ready, setReady] = useState(false)

  useEffect(() => {
    if (!quota.data || ready) return
    setMessageLimit(quota.data.message_limit == null ? '' : String(quota.data.message_limit))
    setStorageLimit(
      quota.data.storage_limit_bytes == null ? '' : String(quota.data.storage_limit_bytes),
    )
    setReady(true)
  }, [quota.data, ready])

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    const message = parseLimit(messageLimit)
    const storage = parseLimit(storageLimit)
    if (message === 'invalid' || storage === 'invalid') {
      setError('上限只能留空或填写非负整数')
      return
    }
    if (!reason.trim()) {
      setError('请填写变更原因')
      return
    }
    setError('')
    try {
      await update.mutateAsync({
        tenantId: tenant.id,
        message_limit: message,
        storage_limit_bytes: storage,
        reason: reason.trim(),
      })
      onFail('')
      onDone(`已更新 ${tenant.name} 的配额`)
      onClose()
    } catch (err) {
      const messageText = errorText(err, '配额保存失败')
      setError(messageText)
      onFail(messageText)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="租户配额"
      description={`${tenant.name} 的消息条数和源文件上限。留空表示不限制，0 表示不能再新增。`}
      busy={update.isPending}
      footer={
        <>
          <Button type="button" variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="tenant-quota"
            loading={update.isPending}
            disabled={quota.isLoading || !reason.trim()}
          >
            保存
          </Button>
        </>
      }
    >
      {quota.isLoading && <p className="text-sm text-text-muted">正在读取上限…</p>}
      {quota.isError && <p className="text-sm text-danger">{errorText(quota.error, '上限读取失败')}</p>}
      <form id="tenant-quota" className="space-y-3" onSubmit={(event) => void onSubmit(event)}>
        <Input
          label="消息条数上限"
          value={messageLimit}
          onChange={(event) => setMessageLimit(event.target.value)}
          inputMode="numeric"
          autoComplete="off"
          hint="留空表示不限制，0 表示不能再新增用户消息。"
        />
        <Input
          label="源文件上限（字节）"
          value={storageLimit}
          onChange={(event) => setStorageLimit(event.target.value)}
          inputMode="numeric"
          autoComplete="off"
          hint="留空表示不限制，0 表示不能再写入新的源文件。"
        />
        <Textarea
          label="变更原因"
          required
          maxLength={200}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          hint="1–200 字，会写入审计。"
        />
        {error && <p className="text-sm text-danger">{error}</p>}
      </form>
    </Modal>
  )
}

function ToggleModal({
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
  const activate = useActivateTenant()
  const pending = deactivate.isPending || activate.isPending
  const enabling = tenant ? !tenant.is_active : false

  return (
    <Modal
      open={tenant !== null}
      onClose={onClose}
      title={enabling ? '启用租户' : '停用租户'}
      description={
        tenant
          ? enabling
            ? `启用「${tenant.name}」后，该租户中的成员可以重新登录。`
            : `停用「${tenant.name}」后，当前正在该租户中的成员不能继续访问。`
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
            disabled={!tenant}
            onClick={() => {
              if (!tenant) return
              const action = enabling ? activate.mutateAsync(tenant.id) : deactivate.mutateAsync(tenant.id)
              void action.then(
                () => {
                  onDone(enabling ? `已启用 ${tenant.name}` : `已停用 ${tenant.name}`)
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
      <p className="text-sm text-text-muted">租户不会被删除。停用时已发出的令牌不会在启用后恢复。</p>
    </Modal>
  )
}
