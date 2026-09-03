/** 定时工作流页：CRUD、手动触发、启停与执行历史。 */

import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import {
  CalendarClock,
  Clock,
  History,
  Pencil,
  Play,
  Plus,
  Timer,
  Trash2,
} from 'lucide-react'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { Input, Textarea } from '@/components/ui/Field'
import { Modal } from '@/components/ui/Modal'
import { Switch } from '@/components/ui/Switch'
import { useToast } from '@/components/ui/Toast'
import {
  useCreateWorkflow,
  useDeleteWorkflow,
  useRunWorkflow,
  useToggleWorkflow,
  useUpdateWorkflow,
  useWorkflowExecutions,
  useWorkflows,
} from '@/api/workflows'
import { ApiError } from '@/lib/http'
import { can } from '@/lib/permissions'
import { cn, formatDateTime, formatDuration, truncate } from '@/lib/cn'
import { useAuthStore } from '@/store/auth'
import type { ExecutionOut, WorkflowOut } from '@/types/api'

const workflowSchema = z.object({
  name: z.string().min(1, '请输入任务名称').max(100, '名称过长'),
  description: z.string().max(500, '描述过长').optional().or(z.literal('')),
  cron_expr: z
    .string()
    .min(1, '请输入 cron 表达式')
    .refine((v) => {
      const parts = v.trim().split(/\s+/)
      return parts.length === 5 || parts.length === 6
    }, 'cron 表达式应为 5 段（分 时 日 月 周）或 6 段（含秒）'),
  prompt_template: z.string().min(1, '请输入 Prompt 模板'),
  timezone: z.string().min(1, '请输入时区'),
  webhook_url: z
    .string()
    .url('请输入合法的 URL')
    .optional()
    .or(z.literal('')),
  enabled: z.boolean(),
})

type WorkflowValues = z.infer<typeof workflowSchema>

const CRON_PRESETS = [
  { label: '每 5 分钟', value: '*/5 * * * *' },
  { label: '每小时整点', value: '0 * * * *' },
  { label: '每天 09:00', value: '0 9 * * *' },
  { label: '每周一 09:00', value: '0 9 * * 1' },
  { label: '每月 1 日 09:00', value: '0 9 1 * *' },
]

const STATUS_TONE: Record<string, 'success' | 'danger' | 'primary' | 'neutral'> = {
  success: 'success',
  failed: 'danger',
  running: 'primary',
  pending: 'neutral',
}

const STATUS_LABEL: Record<string, string> = {
  success: '成功',
  failed: '失败',
  running: '运行中',
  pending: '等待中',
}

function WorkflowFormModal({
  open,
  onClose,
  editing,
}: {
  open: boolean
  onClose: () => void
  editing: WorkflowOut | null
}) {
  const toast = useToast()
  const create = useCreateWorkflow()
  const update = useUpdateWorkflow()

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    reset,
    formState: { errors },
  } = useForm<WorkflowValues>({
    resolver: zodResolver(workflowSchema),
    defaultValues: {
      name: editing?.name ?? '',
      description: editing?.description ?? '',
      cron_expr: editing?.cron_expr ?? '0 9 * * *',
      prompt_template: editing?.prompt_template ?? '',
      timezone: editing?.timezone ?? 'Asia/Shanghai',
      webhook_url: editing?.webhook_url ?? '',
      enabled: editing?.enabled ?? true,
    },
  })

  // 切换编辑目标时重建表单初值
  const key = editing?.id ?? 'new'
  const [lastKey, setLastKey] = useState(key)
  if (key !== lastKey) {
    setLastKey(key)
    reset({
      name: editing?.name ?? '',
      description: editing?.description ?? '',
      cron_expr: editing?.cron_expr ?? '0 9 * * *',
      prompt_template: editing?.prompt_template ?? '',
      timezone: editing?.timezone ?? 'Asia/Shanghai',
      webhook_url: editing?.webhook_url ?? '',
      enabled: editing?.enabled ?? true,
    })
  }

  const cron = watch('cron_expr')
  const enabled = watch('enabled')
  const busy = create.isPending || update.isPending

  async function onSubmit(values: WorkflowValues) {
    const payload = {
      name: values.name,
      cron_expr: values.cron_expr.trim(),
      prompt_template: values.prompt_template,
      description: values.description || null,
      timezone: values.timezone,
      webhook_url: values.webhook_url || null,
      enabled: values.enabled,
    }
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, payload })
        toast.success('已保存', `定时任务《${values.name}》已更新`)
      } else {
        await create.mutateAsync(payload)
        toast.success('已创建', `定时任务《${values.name}》已创建`)
      }
      onClose()
    } catch (err) {
      toast.error(editing ? '保存失败' : '创建失败', err instanceof ApiError ? err.detail : undefined)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? '编辑定时任务' : '新建定时任务'}
      description="任务以创建者身份执行，跨日时区按下方设置解析。"
      size="lg"
      busy={busy}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" loading={busy} onClick={handleSubmit(onSubmit)}>
            {editing ? '保存修改' : '创建任务'}
          </Button>
        </>
      }
    >
      <form className="space-y-4" onSubmit={handleSubmit(onSubmit)} noValidate>
        <Input label="任务名称" required placeholder="例如：每日舆情摘要" error={errors.name?.message} {...register('name')} />
        <Textarea
          label="描述"
          rows={2}
          placeholder="可选，说明这个任务产出的内容与用途"
          error={errors.description?.message}
          {...register('description')}
        />

        <div className="space-y-1.5">
          <div className="flex flex-wrap gap-1.5">
            {CRON_PRESETS.map((p) => (
              <button
                key={p.value}
                type="button"
                onClick={() => setValue('cron_expr', p.value, { shouldValidate: true })}
                className={cn(
                  'min-h-touch rounded-lg border px-3 text-xs transition-colors',
                  cron === p.value
                    ? 'border-primary/50 bg-primary/12 text-primary'
                    : 'border-border bg-surface-2/60 text-text-muted hover:border-border-strong hover:text-text',
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
          <Input
            label="Cron 表达式"
            required
            placeholder="0 9 * * *"
            hint="5 段：分 时 日 月 周；6 段则首段为秒"
            className="font-mono"
            error={errors.cron_expr?.message}
            {...register('cron_expr')}
          />
        </div>

        <Textarea
          label="Prompt 模板"
          required
          rows={6}
          placeholder="例如：请检索知识库中最近一天的更新，输出不超过 300 字的摘要。"
          error={errors.prompt_template?.message}
          {...register('prompt_template')}
        />

        <div className="grid gap-4 sm:grid-cols-2">
          <Input label="时区" required placeholder="Asia/Shanghai" error={errors.timezone?.message} {...register('timezone')} />
          <Input
            label="Webhook 回调地址"
            placeholder="https://example.com/hook"
            hint="可选，执行完成后回调"
            error={errors.webhook_url?.message}
            {...register('webhook_url')}
          />
        </div>

        <div className="rounded-lg border border-border bg-surface-2/50 px-3 py-2.5">
          <Switch
            checked={enabled}
            onChange={(next) => setValue('enabled', next)}
            label="创建后立即启用调度"
          />
        </div>

        <button type="submit" className="hidden" aria-hidden tabIndex={-1} />
      </form>
    </Modal>
  )
}

function ExecutionRow({ execution }: { execution: ExecutionOut }) {
  return (
    <li className="panel-inset space-y-2 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={STATUS_TONE[execution.status] ?? 'neutral'} dot pulse={execution.status === 'running'}>
          {STATUS_LABEL[execution.status] ?? execution.status}
        </Badge>
        <Badge tone="neutral">{execution.triggered_by === 'manual' ? '手动触发' : '定时触发'}</Badge>
        <span className="font-mono text-xs text-text-faint">{execution.duration_ms != null ? formatDuration(execution.duration_ms) : '—'}</span>
        <span className="ml-auto text-xs text-text-faint">{formatDateTime(execution.created_at)}</span>
      </div>
      {execution.error && (
        <p className="rounded-md border border-danger/30 bg-danger/10 px-2.5 py-1.5 text-sm text-danger">
          {execution.error}
        </p>
      )}
      {execution.output && (
        <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-text-muted">
          {truncate(execution.output, 400)}
        </p>
      )}
    </li>
  )
}

function ExecutionsModal({
  workflowId,
  onClose,
}: {
  workflowId: string | null
  onClose: () => void
}) {
  const query = useWorkflowExecutions(workflowId, Boolean(workflowId))
  const executions = query.data ?? []

  return (
    <Modal
      open={Boolean(workflowId)}
      onClose={onClose}
      title="执行历史"
      description="按创建时间倒序，记录触发来源、状态、耗时与输出。"
      size="lg"
      footer={
        <Button variant="ghost" onClick={onClose}>
          关闭
        </Button>
      }
    >
      {query.isLoading ? (
        <SkeletonRows rows={4} />
      ) : query.error ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} className="border-0 bg-transparent" />
      ) : executions.length === 0 ? (
        <EmptyState
          icon={<History className="size-5" aria-hidden />}
          title="还没有执行记录"
          description="等待调度触发，或点击「立即运行」手动执行一次。"
          className="py-10"
        />
      ) : (
        <ul className="space-y-2">
          {executions.map((e) => (
            <ExecutionRow key={e.id} execution={e} />
          ))}
        </ul>
      )}
    </Modal>
  )
}

function WorkflowCard({
  workflow,
  canWrite,
  canDelete,
  onEdit,
  onDelete,
  onRun,
  onToggle,
  onHistory,
  running,
}: {
  workflow: WorkflowOut
  canWrite: boolean
  canDelete: boolean
  running: boolean
  onEdit: (w: WorkflowOut) => void
  onDelete: (w: WorkflowOut) => void
  onRun: (w: WorkflowOut) => void
  onToggle: (w: WorkflowOut, next: boolean) => void
  onHistory: (w: WorkflowOut) => void
}) {
  return (
    <li className="panel grain relative flex flex-col gap-3 overflow-hidden p-4">
      <div className="relative flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-display text-base font-semibold text-text">{workflow.name}</h3>
            {workflow.suspended_owner && (
              <Badge tone="warning" title="创建者已失效，引擎自动停用该任务">
                创建者失效
              </Badge>
            )}
          </div>
          {workflow.description && (
            <p className="mt-1 text-sm text-text-muted">{truncate(workflow.description, 120)}</p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-faint">
            <span className="inline-flex items-center gap-1.5 font-mono">
              <Timer className="size-3.5" aria-hidden />
              {workflow.cron_expr}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Clock className="size-3.5" aria-hidden />
              {workflow.timezone}
            </span>
          </div>
        </div>

        <Switch
          checked={workflow.enabled}
          onChange={(next) => onToggle(workflow, next)}
          disabled={!canWrite}
          label={workflow.enabled ? '已启用' : '已停用'}
        />
      </div>

      <p className="relative line-clamp-2 rounded-lg border border-border/70 bg-surface-2/50 px-3 py-2 text-xs leading-relaxed text-text-muted">
        {truncate(workflow.prompt_template, 160)}
      </p>

      <div className="relative flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="primary"
          disabled={!canWrite}
          loading={running}
          onClick={() => onRun(workflow)}
          icon={<Play className="size-3.5" aria-hidden />}
        >
          立即运行
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onHistory(workflow)}
          icon={<History className="size-3.5" aria-hidden />}
        >
          执行历史
        </Button>
        <div className="ml-auto flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            disabled={!canWrite}
            onClick={() => onEdit(workflow)}
            icon={<Pencil className="size-3.5" aria-hidden />}
          >
            编辑
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={!canDelete}
            onClick={() => onDelete(workflow)}
            icon={<Trash2 className="size-3.5" aria-hidden />}
            className="text-danger hover:bg-danger/10 hover:text-danger"
          >
            删除
          </Button>
        </div>
      </div>
    </li>
  )
}

export default function WorkflowsPage() {
  const toast = useToast()
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = can(role, 'workflows', 'write')
  const canDelete = can(role, 'workflows', 'delete')

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<WorkflowOut | null>(null)
  const [pendingDelete, setPendingDelete] = useState<WorkflowOut | null>(null)
  const [historyId, setHistoryId] = useState<string | null>(null)
  const [runningId, setRunningId] = useState<string | null>(null)

  const workflows = useWorkflows()
  const remove = useDeleteWorkflow()
  const run = useRunWorkflow()
  const toggle = useToggleWorkflow()

  const list = workflows.data ?? []
  const disabled = (workflows.error as { status?: number } | null)?.status === 503

  async function handleRun(w: WorkflowOut) {
    setRunningId(w.id)
    try {
      const execution = await run.mutateAsync(w.id)
      toast.success(
        execution.status === 'success' ? '执行完成' : '执行结束',
        `状态：${STATUS_LABEL[execution.status] ?? execution.status} · 耗时 ${formatDuration(execution.duration_ms)}`,
      )
    } catch (err) {
      toast.error('执行失败', err instanceof ApiError ? err.detail : undefined)
    } finally {
      setRunningId(null)
    }
  }

  async function handleToggle(w: WorkflowOut, next: boolean) {
    try {
      await toggle.mutateAsync({ id: w.id, enabled: next })
      toast.success(next ? '已启用调度' : '已停用调度')
    } catch (err) {
      toast.error('操作失败', err instanceof ApiError ? err.detail : undefined)
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return
    try {
      await remove.mutateAsync(pendingDelete.id)
      toast.success('任务已删除')
    } catch (err) {
      toast.error('删除失败', err instanceof ApiError ? err.detail : undefined)
    } finally {
      setPendingDelete(null)
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="定时工作流"
        description="以 cron 周期调用 Agent 执行 Prompt 模板，任务以创建者身份运行并留存执行历史。"
        actions={
          canWrite ? (
            <Button
              variant="primary"
              onClick={() => {
                setEditing(null)
                setFormOpen(true)
              }}
              icon={<Plus className="size-4" aria-hidden />}
            >
              新建任务
            </Button>
          ) : undefined
        }
      />

      {workflows.isLoading ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="panel p-4">
            <SkeletonRows rows={4} />
          </div>
          <div className="panel p-4">
            <SkeletonRows rows={4} />
          </div>
        </div>
      ) : workflows.error ? (
        <ErrorState
          error={workflows.error}
          onRetry={() => void workflows.refetch()}
          title={disabled ? '工作流引擎未启用' : undefined}
        />
      ) : list.length === 0 ? (
        <div className="panel">
          <EmptyState
            icon={<CalendarClock className="size-5" aria-hidden />}
            title="还没有定时任务"
            description="把重复性的 Prompt 变成日程：设定 cron 表达式后，引擎会按周期自动执行。"
            action={
              canWrite ? (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => {
                    setEditing(null)
                    setFormOpen(true)
                  }}
                >
                  新建第一个任务
                </Button>
              ) : undefined
            }
          />
        </div>
      ) : (
        <ul className="grid gap-4 lg:grid-cols-2">
          {list.map((w) => (
            <WorkflowCard
              key={w.id}
              workflow={w}
              canWrite={canWrite}
              canDelete={canDelete}
              running={runningId === w.id}
              onEdit={(item) => {
                setEditing(item)
                setFormOpen(true)
              }}
              onDelete={setPendingDelete}
              onRun={(item) => void handleRun(item)}
              onToggle={(item, next) => void handleToggle(item, next)}
              onHistory={(item) => setHistoryId(item.id)}
            />
          ))}
        </ul>
      )}

      <WorkflowFormModal
        open={formOpen}
        editing={editing}
        onClose={() => {
          setFormOpen(false)
          setEditing(null)
        }}
      />

      <ExecutionsModal workflowId={historyId} onClose={() => setHistoryId(null)} />

      <Modal
        open={Boolean(pendingDelete)}
        onClose={() => setPendingDelete(null)}
        title="删除定时任务"
        description="任务及其执行历史将一并移除，此操作不可撤销。"
        size="sm"
        busy={remove.isPending}
        footer={
          <>
            <Button variant="ghost" onClick={() => setPendingDelete(null)}>
              取消
            </Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => void confirmDelete()}>
              确认删除
            </Button>
          </>
        }
      >
        <p className="text-sm text-text-muted">{pendingDelete?.name}</p>
      </Modal>

      <p className="text-center text-xs text-text-faint">
        创建、更新、删除与手动执行均写入审计日志。
      </p>
    </div>
  )
}
