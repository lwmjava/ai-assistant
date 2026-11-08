/** 当前用户查看、纠正、失效和导出自己的跨会话记忆。 */

import { useState } from 'react'
import { Download } from 'lucide-react'

import {
  downloadMemoryExport,
  useCorrectMemory,
  useInvalidateMemory,
  useMemories,
  useMemorySwitch,
  useUpdateMemorySwitch,
} from '@/api/memories'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { Switch } from '@/components/ui/Switch'
import { ApiError } from '@/lib/http'

const GRADE: Record<string, string> = {
  user_statement: '用户陈述',
  model_inference: '模型推断',
}

export default function MemoriesPage() {
  const settings = useMemorySwitch()
  const memories = useMemories()
  const updateSwitch = useUpdateMemorySwitch()
  const correct = useCorrectMemory()
  const invalidate = useInvalidateMemory()
  const [selected, setSelected] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  const rows = memories.data ?? []
  const current = rows.find((row) => row.id === selected) ?? rows[0] ?? null
  const effective = settings.data?.effective ?? false

  async function onExport() {
    setError('')
    try {
      await downloadMemoryExport()
      setNotice('已开始下载')
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : '导出失败')
    }
  }

  async function onCorrect() {
    if (!current) return
    setError('')
    setNotice('')
    try {
      const saved = await correct.mutateAsync({ id: current.id, content: draft || current.content })
      setSelected(saved.id)
      setDraft('')
      setNotice('已保存新版本')
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : '纠正失败')
    }
  }

  async function onInvalidate() {
    if (!current) return
    setError('')
    setNotice('')
    try {
      await invalidate.mutateAsync(current.id)
      setSelected(null)
      setNotice('已不再检索这条记忆，记录仍保留')
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : '操作失败')
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title="我的记忆"
        description="只显示你自己明确保存的跨会话记忆。关闭开关后停止读写，已有记录保留。"
        actions={
          <Button type="button" icon={<Download className="size-4" aria-hidden />} onClick={() => void onExport()}>
            导出
          </Button>
        }
      />

      <section className="panel space-y-3 px-4 py-4">
        {settings.isLoading ? (
          <SkeletonRows rows={1} />
        ) : settings.isError ? (
          <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />
        ) : (
          <>
            <Switch
              checked={Boolean(settings.data?.user_enabled)}
              disabled={updateSwitch.isPending}
              label="允许读写我的跨会话记忆"
              onChange={(next) => {
                setError('')
                void updateSwitch.mutateAsync(next).catch((err: unknown) => {
                  setError(err instanceof ApiError ? err.detail : '开关保存失败')
                })
              }}
            />
            {settings.data && !settings.data.deployment_enabled && (
              <p className="text-sm text-text-muted">部署未开启。打开个人开关也不会读写。</p>
            )}
            {settings.data && settings.data.deployment_enabled && !settings.data.user_enabled && (
              <p className="text-sm text-text-muted">你已关闭自己的读写。列表仍可查看、失效和导出。</p>
            )}
          </>
        )}
      </section>

      {notice && <p className="text-sm text-primary">{notice}</p>}
      {error && <p className="text-sm text-danger">{error}</p>}

      {memories.isLoading ? (
        <SkeletonRows rows={3} />
      ) : memories.isError ? (
        <ErrorState error={memories.error} onRetry={() => void memories.refetch()} />
      ) : rows.length === 0 ? (
        <div className="panel">
          <EmptyState title="还没有可管理的记忆" description="在对话里明确保存后，会出现在这里。" />
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
          <ul className="panel divide-y divide-border">
            {rows.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  className="w-full px-4 py-3 text-left text-sm hover:bg-surface-2"
                  onClick={() => {
                    setSelected(row.id)
                    setDraft('')
                  }}
                >
                  <span className="text-text-muted">{GRADE[row.source_grade] ?? row.source_grade}</span>
                  <span className="mt-1 block truncate text-text">{row.content}</span>
                </button>
              </li>
            ))}
          </ul>
          {current && (
            <section className="panel space-y-3 px-4 py-4">
              <p className="text-sm text-text-muted">{GRADE[current.source_grade] ?? current.source_grade}</p>
              <p className="whitespace-pre-wrap text-sm text-text">{current.content}</p>
              <label className="block space-y-1 text-sm text-text-muted" htmlFor="memory-correction">
                纠正为
                <textarea
                  id="memory-correction"
                  className="mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
                  rows={4}
                  value={draft}
                  disabled={!effective || correct.isPending}
                  onChange={(event) => setDraft(event.target.value)}
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="primary" disabled={!effective || correct.isPending} onClick={() => void onCorrect()}>
                  保存新版本
                </Button>
                <Button type="button" variant="danger" disabled={invalidate.isPending} onClick={() => void onInvalidate()}>
                  不再检索
                </Button>
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  )
}
