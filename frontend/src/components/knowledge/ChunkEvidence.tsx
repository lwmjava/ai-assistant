/**
 * 引用原文核验面板：展示命中块正文与定位信息（ADR-0007 只读核验）。
 *
 * 权限完全由后端 `GET /api/rag/chunks/{id}/evidence` 每次请求独立复核：
 * 这里只在展开后才发请求，拿到什么显示什么，不在前端预判断权后隐藏请求。
 * 无权限与已失效都回到同一句后端文案，界面上统一呈现为「核验不可用」。
 */

import { useState } from 'react'
import { FileSearch } from 'lucide-react'

import { useChunkEvidence } from '@/api/rag'
import { ChunkContent } from '@/components/knowledge/ChunkContent'
import { Badge } from '@/components/ui/Badge'
import { ErrorState } from '@/components/ui/Feedback'
import type { ChunkLocatorOut } from '@/types/api'

const VERSION_LABELS: Record<string, string> = {
  draft: '草稿',
  scheduled: '待生效',
  published: '已发布',
  replaced: '已替换',
  archived: '已归档',
}

function LocatorRows({ label, locator }: { label: string; locator: ChunkLocatorOut }) {
  return (
    <>
      <div className="flex flex-wrap gap-x-2">
        <dt>
          {label}块序
        </dt>
        <dd className="font-mono">#{locator.chunk_index + 1}</dd>
      </div>
      <div className="flex flex-wrap gap-x-2">
        <dt>{label}页码</dt>
        <dd className="font-mono">{locator.page ?? '未提供'}</dd>
      </div>
      <div className="flex flex-wrap gap-x-2">
        <dt>{label}段落</dt>
        <dd>{locator.section ?? '未提供'}</dd>
      </div>
      <div className="flex flex-wrap gap-x-2">
        <dt>{label}源范围</dt>
        <dd className="font-mono">
          {locator.source_start != null && locator.source_end != null
            ? `${locator.source_start}–${locator.source_end}`
            : '未提供'}
        </dd>
      </div>
    </>
  )
}

/**
 * 核验入口。命中块与文档分块共用：点开才请求，失败给出明确反馈。
 *
 * 只显示块本身；父块默认只有定位信息，正文要后端独立鉴权通过才会出现。
 */
export function ChunkEvidence({ chunkId }: { chunkId: string | null }) {
  const [open, setOpen] = useState(false)
  const evidence = useChunkEvidence(open ? chunkId : null)

  if (!chunkId) {
    return <p className="text-xs text-text-faint">该结果未提供块 ID，无法核验原文。</p>
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-primary hover:bg-primary/10"
      >
        <FileSearch className="size-3.5" aria-hidden />
        {open ? '收起原文核验' : '核验原文'}
      </button>

      {open && (
        <div className="rounded-lg border border-border bg-surface-2/40 px-3 py-2">
          {evidence.isLoading && <p className="text-xs text-text-faint">正在核验原文…</p>}
          {evidence.error && (
            <ErrorState
              title="引用原文不可用"
              error={evidence.error}
              onRetry={() => void evidence.refetch()}
              className="border-0 bg-transparent py-4"
            />
          )}
          {evidence.data && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium text-text-muted">
                  {evidence.data.document_title}
                </span>
                <Badge tone={evidence.data.version_state === 'published' ? 'success' : 'warning'}>
                  {VERSION_LABELS[evidence.data.version_state] ?? evidence.data.version_state}
                </Badge>
              </div>
              <dl className="space-y-1 text-sm text-text-muted">
                <LocatorRows label="" locator={evidence.data} />
                {evidence.data.source && (
                  <div className="flex flex-wrap gap-x-2">
                    <dt>来源</dt>
                    <dd>{evidence.data.source}</dd>
                  </div>
                )}
              </dl>
              <ChunkContent content={evidence.data.content} />
              {evidence.data.parent && (
                <div className="border-t border-border/60 pt-2">
                  <p className="mb-1 text-xs font-medium text-text-faint">
                    父块定位（独立鉴权；默认不含父正文）
                  </p>
                  <dl className="space-y-1 text-sm text-text-muted">
                    <LocatorRows label="父" locator={evidence.data.parent} />
                  </dl>
                  {evidence.data.parent.content && <ChunkContent content={evidence.data.parent.content} />}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
