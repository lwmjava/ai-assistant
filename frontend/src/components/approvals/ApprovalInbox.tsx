/** 审批待办。按钮只提交服务端已有的状态，不在这里判断风险。 */

import { useEffect, useState } from 'react'

import {
  useApprovalEvents,
  useApproveAction,
  useExecuteAction,
  useRejectAction,
} from '@/api/approvals'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState } from '@/components/ui/Feedback'
import { ApiError } from '@/lib/http'
import {
  approvalChoices,
  approvalStatusLabel,
  visibleApprovals,
  type ApprovalEvent,
} from '@/components/approvals/state'

function errorText(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return '这条审批不存在或已不能查看'
  if (error instanceof ApiError) return error.detail
  return '操作失败'
}

export function ApprovalInbox({ openOnly = false }: { openOnly?: boolean }) {
  const events = useApprovalEvents()
  const { refetch } = events
  const approve = useApproveAction()
  const reject = useRejectAction()
  const execute = useExecuteAction()
  const [busyId, setBusyId] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refetch()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [refetch])

  const rows = visibleApprovals(events.data ?? [], openOnly)

  async function onExecute(row: ApprovalEvent) {
    setBusyId(row.id)
    setError('')
    setNotice('')
    try {
      if (row.status === 'pending') await approve.mutateAsync(row.id)
      const done = await execute.mutateAsync(row.id)
      setNotice(
        done.execution_count > 0
          ? `已记录执行 ${done.execution_count} 次${done.result_code ? `，结果 ${done.result_code}` : ''}`
          : approvalStatusLabel(done.status),
      )
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusyId(null)
    }
  }

  async function onReject(row: ApprovalEvent) {
    setBusyId(row.id)
    setError('')
    setNotice('')
    try {
      const done = await reject.mutateAsync(row.id)
      setNotice(approvalStatusLabel(done.status))
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusyId(null)
    }
  }

  if (events.isLoading) {
    return openOnly ? null : <p className="text-sm text-text-muted">正在读取待审批…</p>
  }
  if (events.isError) {
    return openOnly ? (
      <p className="mb-2 text-sm text-danger">{errorText(events.error)}</p>
    ) : (
      <ErrorState error={events.error} onRetry={() => void events.refetch()} />
    )
  }
  if (rows.length === 0) {
    return openOnly ? null : (
      <EmptyState title="没有待审批的动作" description="刷新或重新连接后，仍只会看到你自己发起的记录。" />
    )
  }

  return (
    <div className={openOnly ? 'mb-3 space-y-2' : 'space-y-2'}>
      {openOnly && (
        <p className="text-xs text-text-muted">这些动作需要你确认。在输入框里回复不会批准。</p>
      )}
      {rows.map((row) => {
        const choices = approvalChoices(row.status)
        const busy = busyId === row.id
        return (
          <article key={row.id} className="rounded-lg border border-border bg-surface-2/70 p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0 space-y-1">
                <p className="text-sm font-medium text-text">{row.subject || row.id}</p>
                <p className="text-sm text-text-muted">{row.parameter_summary || '无参数摘要'}</p>
                <p className="text-xs text-text-muted">
                  {approvalStatusLabel(row.status)}
                  {row.execution_count > 0 ? ` · 已执行 ${row.execution_count} 次` : ''}
                </p>
              </div>
              <div className="flex gap-2">
                {choices.reject && (
                  <Button size="sm" variant="danger" disabled={busy} onClick={() => void onReject(row)}>
                    拒绝
                  </Button>
                )}
                {choices.execute && (
                  <Button size="sm" loading={busy} onClick={() => void onExecute(row)}>
                    {row.status === 'executed' ? '再次查看' : '执行'}
                  </Button>
                )}
              </div>
            </div>
          </article>
        )
      })}
      {notice && <p className="text-sm text-text-muted">{notice}</p>}
      {error && <p className="text-sm text-danger">{error}</p>}
    </div>
  )
}
