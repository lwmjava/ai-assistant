/** 技能表格：筛选和新增在表格上方，行内操作在最后一列。 */

import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'

import {
  useCreateSkill,
  useDeleteSkill,
  useSetSkillEnabled,
  useSkillDetail,
  useSkills,
  useSkillTenants,
  useUpdateSkill,
} from '@/api/skills'
import type { SkillFilters } from '@/api/skills'
import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState } from '@/components/ui/Feedback'
import { Input, Select, Textarea } from '@/components/ui/Field'
import { Modal } from '@/components/ui/Modal'
import { ApiError } from '@/lib/http'
import { can } from '@/lib/permissions'
import { useAuthStore } from '@/store/auth'
import type { SkillListOut, SkillWrite } from '@/types/api'

const EMPTY_FILTERS: SkillFilters = { q: '', scope: '', enabled: '', tenantId: '' }

interface Draft {
  name: string
  description: string
  keywords: string
  constraints: string
  system_prompt: string
  example: string
  scope: 'private' | 'global'
}

const EMPTY_DRAFT: Draft = {
  name: '',
  description: '',
  keywords: '',
  constraints: '',
  system_prompt: '',
  example: '',
  scope: 'private',
}

function splitKeywords(raw: string): string[] {
  return raw
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function toWrite(draft: Draft, includeScope: boolean): SkillWrite {
  const body: SkillWrite = {
    name: draft.name.trim(),
    description: draft.description,
    keywords: splitKeywords(draft.keywords),
    constraints: draft.constraints,
    system_prompt: draft.system_prompt,
    example: draft.example,
  }
  if (includeScope) body.scope = draft.scope
  return body
}

function errorText(err: unknown): string {
  if (err instanceof ApiError && err.detail) return err.detail
  return '操作失败，请稍后重试'
}

function scopeLabel(scope: string): string {
  if (scope === 'global') return '系统全局'
  if (scope === 'builtin') return '内置'
  return '私有'
}

export default function SkillsPage() {
  const role = useAuthStore((s) => s.user?.role)
  const userId = useAuthStore((s) => s.user?.id)
  const canWrite = can(role, 'skills', 'write')
  const canDelete = can(role, 'skills', 'delete')
  const broad = role === 'system_admin' || role === 'system_viewer'
  const [filters, setFilters] = useState<SkillFilters>(EMPTY_FILTERS)
  const [draftFilters, setDraftFilters] = useState<SkillFilters>(EMPTY_FILTERS)
  const skills = useSkills(filters)
  const tenants = useSkillTenants(role === 'system_admin')
  const [editor, setEditor] = useState<{ mode: 'create' | 'edit'; id?: string; draft: Draft } | null>(
    null,
  )
  const [detailId, setDetailId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<SkillListOut | null>(null)
  const [formError, setFormError] = useState('')
  const detail = useSkillDetail(detailId)
  const editDetail = useSkillDetail(editor?.mode === 'edit' ? (editor.id ?? null) : null)

  useEffect(() => {
    if (!editDetail.data || editor?.mode !== 'edit' || editor.id !== editDetail.data.id) return
    if (editor.draft.system_prompt) return
    setEditor({
      ...editor,
      draft: {
        ...editor.draft,
        constraints: editDetail.data.constraints,
        system_prompt: editDetail.data.system_prompt,
        example: editDetail.data.example,
      },
    })
  }, [editDetail.data, editor])
  const createSkill = useCreateSkill()
  const updateSkill = useUpdateSkill()
  const setEnabled = useSetSkillEnabled()
  const removeSkill = useDeleteSkill()

  function openCreate() {
    setFormError('')
    setEditor({ mode: 'create', draft: { ...EMPTY_DRAFT } })
  }

  function openEdit(row: SkillListOut) {
    setFormError('')
    setEditor({
      mode: 'edit',
      id: row.id,
      draft: {
        name: row.name,
        description: row.description,
        keywords: row.keywords.join('，'),
        constraints: '',
        system_prompt: '',
        example: '',
        scope: row.scope === 'global' ? 'global' : 'private',
      },
    })
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault()
    setFilters({ ...draftFilters })
  }

  async function submitEditor(event: FormEvent) {
    event.preventDefault()
    if (!editor) return
    setFormError('')
    try {
      if (editor.mode === 'create') {
        await createSkill.mutateAsync(toWrite(editor.draft, role === 'system_admin'))
      } else if (editor.id) {
        await updateSkill.mutateAsync({ id: editor.id, body: toWrite(editor.draft, false) })
      }
      setEditor(null)
    } catch (err) {
      setFormError(errorText(err))
    }
  }

  const rows = skills.data ?? []

  return (
    <div className="space-y-4">
      <PageHeader
        title="技能"
        description="按条件查看技能。新增在表格上方，每条记录的操作在最后一列。"
      />

      <form
        className="flex flex-wrap items-end justify-between gap-3"
        onSubmit={applyFilters}
      >
        <div className="flex flex-wrap items-end gap-3">
          <Input
            label="名称"
            value={draftFilters.q}
            onChange={(event) => setDraftFilters((prev) => ({ ...prev, q: event.target.value }))}
            placeholder="搜索名称或说明"
            wrapClassName="w-56"
          />
          <Select
            label="范围"
            value={draftFilters.scope}
            onChange={(event) =>
              setDraftFilters((prev) => ({
                ...prev,
                scope: event.target.value as SkillFilters['scope'],
              }))
            }
            wrapClassName="w-36"
          >
            <option value="">全部</option>
            <option value="private">私有</option>
            <option value="global">系统全局</option>
            <option value="builtin">内置</option>
          </Select>
          <Select
            label="状态"
            value={draftFilters.enabled}
            onChange={(event) =>
              setDraftFilters((prev) => ({
                ...prev,
                enabled: event.target.value as SkillFilters['enabled'],
              }))
            }
            wrapClassName="w-32"
          >
            <option value="">全部</option>
            <option value="true">启用</option>
            <option value="false">停用</option>
          </Select>
          {role === 'system_admin' && (
            <Select
              label="租户"
              value={draftFilters.tenantId}
              onChange={(event) =>
                setDraftFilters((prev) => ({ ...prev, tenantId: event.target.value }))
              }
              wrapClassName="w-48"
            >
              <option value="">全部租户</option>
              {(tenants.data ?? []).map((tenant) => (
                <option key={tenant.id} value={tenant.id}>
                  {tenant.name}
                </option>
              ))}
            </Select>
          )}
          {role === 'system_viewer' && (
            <Input
              label="租户编号"
              value={draftFilters.tenantId}
              onChange={(event) =>
                setDraftFilters((prev) => ({ ...prev, tenantId: event.target.value }))
              }
              wrapClassName="w-48"
            />
          )}
          <Button type="submit" variant="secondary">
            筛选
          </Button>
        </div>
        {canWrite && (
          <Button type="button" variant="primary" onClick={openCreate}>
            新增技能
          </Button>
        )}
      </form>

      <div className="panel overflow-x-auto">
        {skills.isLoading && <p className="px-4 py-8 text-sm text-text-muted">正在加载技能…</p>}
        {skills.isError && <ErrorState error={skills.error} onRetry={() => skills.refetch()} />}
        {skills.isSuccess && rows.length === 0 && (
          <EmptyState
            title="没有符合条件的技能"
            description="调整筛选条件，或新增一条技能。"
          />
        )}
        {skills.isSuccess && rows.length > 0 && (
          <table className="w-full min-w-[880px] text-left text-sm">
            <thead className="border-b border-border bg-surface-2/70 text-text-muted">
              <tr>
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">说明</th>
                <th className="px-4 py-3 font-medium">范围</th>
                {broad && <th className="px-4 py-3 font-medium">租户</th>}
                {broad && <th className="px-4 py-3 font-medium">创建者</th>}
                <th className="px-4 py-3 font-medium">关键词</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <SkillRow
                  key={row.id}
                  row={row}
                  broad={broad}
                  canWrite={canWrite}
                  canDelete={canDelete}
                  isOwner={row.owner_id === userId}
                  isAdmin={role === 'system_admin'}
                  isTenantAdmin={role === 'tenant_admin'}
                  busy={setEnabled.isPending || removeSkill.isPending}
                  onDetail={() => setDetailId(row.id)}
                  onEdit={() => openEdit(row)}
                  onToggle={() => setEnabled.mutate({ id: row.id, enabled: !row.enabled })}
                  onDelete={() => setDeleteTarget(row)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Modal
        open={editor !== null}
        onClose={() => setEditor(null)}
        title={editor?.mode === 'edit' ? '修改技能' : '新增技能'}
        size="lg"
        busy={createSkill.isPending || updateSkill.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditor(null)}>
              取消
            </Button>
            <Button
              variant="primary"
              type="submit"
              form="skill-editor"
              loading={createSkill.isPending || updateSkill.isPending}
            >
              保存
            </Button>
          </>
        }
      >
        {editor && (
          <form id="skill-editor" className="space-y-3" onSubmit={submitEditor}>
            {editor.mode === 'edit' && editDetail.isLoading && (
              <p className="text-sm text-text-muted">正在读取技能正文…</p>
            )}
            <Input
              label="名称"
              required
              value={editor.draft.name}
              onChange={(event) =>
                setEditor({ ...editor, draft: { ...editor.draft, name: event.target.value } })
              }
            />
            <Textarea
              label="说明"
              required
              value={editor.draft.description}
              onChange={(event) =>
                setEditor({
                  ...editor,
                  draft: { ...editor.draft, description: event.target.value },
                })
              }
            />
            <Input
              label="关键词"
              required
              hint="用逗号分隔，1–8 个"
              value={editor.draft.keywords}
              onChange={(event) =>
                setEditor({ ...editor, draft: { ...editor.draft, keywords: event.target.value } })
              }
            />
            <Textarea
              label="约束"
              required
              value={editor.draft.constraints}
              onChange={(event) =>
                setEditor({
                  ...editor,
                  draft: { ...editor.draft, constraints: event.target.value },
                })
              }
            />
            <Textarea
              label="技能说明"
              required
              value={editor.draft.system_prompt}
              onChange={(event) =>
                setEditor({
                  ...editor,
                  draft: { ...editor.draft, system_prompt: event.target.value },
                })
              }
            />
            <Textarea
              label="示例"
              required
              value={editor.draft.example}
              onChange={(event) =>
                setEditor({ ...editor, draft: { ...editor.draft, example: event.target.value } })
              }
            />
            {editor.mode === 'create' && role === 'system_admin' && (
              <Select
                label="生效范围"
                value={editor.draft.scope}
                onChange={(event) =>
                  setEditor({
                    ...editor,
                    draft: {
                      ...editor.draft,
                      scope: event.target.value === 'global' ? 'global' : 'private',
                    },
                  })
                }
              >
                <option value="private">仅自己</option>
                <option value="global">系统全局</option>
              </Select>
            )}
            {formError && <p className="text-sm text-danger">{formError}</p>}
          </form>
        )}
      </Modal>

      <Modal
        open={detailId !== null && editor === null}
        onClose={() => setDetailId(null)}
        title="技能详情"
        size="lg"
      >
        {detail.isLoading && <p className="text-sm text-text-muted">正在读取…</p>}
        {detail.isError && <ErrorState error={detail.error} />}
        {detail.data && (
          <div className="space-y-3 text-sm">
            <p>
              <span className="text-text-muted">名称：</span>
              {detail.data.name}
            </p>
            <p>
              <span className="text-text-muted">说明：</span>
              {detail.data.description}
            </p>
            <p>
              <span className="text-text-muted">约束：</span>
              {detail.data.constraints || '无'}
            </p>
            <p className="whitespace-pre-wrap">
              <span className="text-text-muted">技能说明：</span>
              {detail.data.system_prompt || '无'}
            </p>
            <p>
              <span className="text-text-muted">示例：</span>
              {detail.data.example || '无'}
            </p>
          </div>
        )}
      </Modal>

      <Modal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="删除技能"
        description={deleteTarget ? `确定删除「${deleteTarget.name}」？删除后不能恢复。` : undefined}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleteTarget(null)}>
              取消
            </Button>
            <Button
              variant="danger"
              loading={removeSkill.isPending}
              onClick={async () => {
                if (!deleteTarget) return
                try {
                  await removeSkill.mutateAsync(deleteTarget.id)
                  setDeleteTarget(null)
                } catch (err) {
                  setFormError(errorText(err))
                  setDeleteTarget(null)
                }
              }}
            >
              删除
            </Button>
          </>
        }
      >
        {formError && deleteTarget === null ? null : null}
      </Modal>
    </div>
  )
}

function SkillRow({
  row,
  broad,
  canWrite,
  canDelete,
  isOwner,
  isAdmin,
  isTenantAdmin,
  busy,
  onDetail,
  onEdit,
  onToggle,
  onDelete,
}: {
  row: SkillListOut
  broad: boolean
  canWrite: boolean
  canDelete: boolean
  isOwner: boolean
  isAdmin: boolean
  isTenantAdmin: boolean
  busy: boolean
  onDetail: () => void
  onEdit: () => void
  onToggle: () => void
  onDelete: () => void
}) {
  const builtin = row.source === 'builtin'
  const ownPrivate = isOwner && row.scope === 'private'
  const showEdit = canWrite && !builtin && (isAdmin || ownPrivate)
  const showToggle =
    canWrite &&
    !builtin &&
    (isAdmin || ownPrivate || (isTenantAdmin && row.scope === 'private'))
  const showDelete = canDelete && !builtin && (isAdmin || ownPrivate)

  return (
    <tr className="border-b border-border/70 last:border-0">
      <td className="px-4 py-3 font-medium text-text">{row.name}</td>
      <td className="max-w-xs truncate px-4 py-3 text-text-muted">{row.description}</td>
      <td className="px-4 py-3">
        <Badge tone={row.scope === 'global' ? 'primary' : 'neutral'}>{scopeLabel(row.scope)}</Badge>
      </td>
      {broad && <td className="px-4 py-3 text-text-muted">{row.tenant_name || '—'}</td>}
      {broad && <td className="px-4 py-3 text-text-muted">{row.owner_username || '—'}</td>}
      <td className="px-4 py-3 text-text-muted">{row.keywords.join('、')}</td>
      <td className="px-4 py-3">
        <Badge tone={row.enabled ? 'success' : 'warning'}>{row.enabled ? '启用' : '停用'}</Badge>
      </td>
      <td className="px-4 py-3">
        <div className="flex justify-end gap-1 whitespace-nowrap">
          <Button size="sm" variant="ghost" onClick={onDetail}>
            详情
          </Button>
          {showEdit && (
            <Button size="sm" variant="ghost" onClick={onEdit}>
              修改
            </Button>
          )}
          {showToggle && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={onToggle}>
              {row.enabled ? '停用' : '启用'}
            </Button>
          )}
          {showDelete && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={onDelete}>
              删除
            </Button>
          )}
        </div>
      </td>
    </tr>
  )
}
