/** 知识库页：文本摄取、文件上传、混合检索与文档管理。 */

import { useMemo, useRef, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { FileText, FileUp, Plus, Search, Trash2 } from 'lucide-react'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { Input, Textarea } from '@/components/ui/Field'
import { Modal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import { createConfirmation, useDeleteDocument, useDocuments, useIngestDocument, usePublishDocument, useSearch, useUploadDocument } from '@/api/rag'
import { ApiError, isSessionExpiredError } from '@/lib/http'
import { can } from '@/lib/permissions'
import { cn, formatDateTime, timeAgo } from '@/lib/cn'
import { useAuthStore } from '@/store/auth'
import type { DocumentOut, SearchResultOut } from '@/types/api'

const VERSION_LABELS: Record<string, string> = {
  draft: '草稿',
  scheduled: '待生效',
  published: '已发布',
  replaced: '已替换',
  archived: '已归档',
}

const ingestSchema = z.object({
  title: z.string().min(1, '请输入标题').max(200, '标题过长'),
  source: z.string().max(200, '来源过长').optional().or(z.literal('')),
  text: z.string().min(1, '请输入正文内容'),
})

type IngestValues = z.infer<typeof ingestSchema>

const searchSchema = z.object({
  query: z.string().min(1, '请输入检索内容'),
  top_k: z.coerce.number().int().min(1, '至少 1 条').max(50, '最多 50 条'),
})

type SearchValues = z.infer<typeof searchSchema>

function IngestModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  const ingest = useIngestDocument()
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<IngestValues>({
    resolver: zodResolver(ingestSchema),
    defaultValues: { title: '', source: '', text: '' },
  })

  async function onSubmit(values: IngestValues) {
    try {
      const doc = await ingest.mutateAsync({
        title: values.title,
        text: values.text,
        source: values.source || undefined,
      })
      toast.success('已摄取', `《${doc.title}》切分为 ${doc.chunk_count} 个分块`)
      reset()
      onClose()
    } catch (err) {
      if (isSessionExpiredError(err)) return
      toast.error('摄取失败', err instanceof ApiError ? err.detail : undefined)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="摄取文本"
      description="文本将自动分块并生成嵌入向量，随后可被对话检索命中。"
      size="lg"
      busy={ingest.isPending}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" loading={ingest.isPending} onClick={handleSubmit(onSubmit)}>
            开始摄取
          </Button>
        </>
      }
    >
      <form className="space-y-4" onSubmit={handleSubmit(onSubmit)} noValidate>
        <Input label="标题" required placeholder="例如：产品定价说明" error={errors.title?.message} {...register('title')} />
        <Input label="来源" placeholder="例如：内部知识库 / 手册第 3 章" hint="可选，用于结果溯源" error={errors.source?.message} {...register('source')} />
        <Textarea
          label="正文"
          required
          rows={10}
          placeholder="粘贴要纳入知识库的文本内容…"
          error={errors.text?.message}
          {...register('text')}
        />
        {/* 表单内回车提交时需存在 submit 按钮，这里隐藏以避免布局干扰 */}
        <button type="submit" className="hidden" aria-hidden tabIndex={-1} />
      </form>
    </Modal>
  )
}

function SearchPanel() {
  const search = useSearch()
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<SearchValues>({
    resolver: zodResolver(searchSchema),
    defaultValues: { query: '', top_k: 5 },
  })

  const results = search.data ?? []

  return (
    <section className="panel grain relative overflow-hidden p-4 sm:p-5">
      <div className="relative space-y-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <Input
            placeholder="检索知识库，例如：退款政策是怎样的"
            error={errors.query?.message}
            wrapClassName="flex-1"
            {...register('query')}
          />
          <div className="flex gap-2">
            <Input
              type="number"
              min={1}
              max={50}
              label="返回条数"
              wrapClassName="w-28"
              error={errors.top_k?.message}
              {...register('top_k')}
            />
            <Button
              variant="primary"
              loading={search.isPending}
              onClick={handleSubmit((v) => search.mutate({ query: v.query, top_k: v.top_k }))}
              icon={<Search className="size-4" aria-hidden />}
              className="mb-[1.375rem] sm:mb-0"
            >
              检索
            </Button>
          </div>
        </div>

        {search.error && !isSessionExpiredError(search.error) && (
          <ErrorState error={search.error} onRetry={() => search.reset()} className="border-0 bg-transparent py-6" />
        )}

        {search.isSuccess && results.length === 0 && (
          <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-text-faint">
            没有命中任何分块，换个说法或先摄取相关文档试试。
          </p>
        )}

        {results.length > 0 && (
          <ul className="space-y-2">
            {results.map((r, i) => (
              <SearchResultRow key={`${r.document_id}-${i}`} result={r} index={i} />
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

function SearchResultRow({ result, index }: { result: SearchResultOut; index: number }) {
  return (
    <li className="panel-inset p-3">
      <div className="mb-1.5 flex items-center gap-2">
        <Badge tone="primary">#{index + 1}</Badge>
        <span className="font-mono text-xs text-text-faint">score {result.score.toFixed(4)}</span>
        {result.source && (
          <span className="truncate text-xs text-text-faint">来源：{result.source}</span>
        )}
      </div>
      <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-text-muted">
        {result.content}
      </p>
    </li>
  )
}

function DocumentRow({
  doc,
  canDelete,
  canPublish,
  onDelete,
  onPublish,
}: {
  doc: DocumentOut
  canDelete: boolean
  canPublish: boolean
  onDelete: (doc: DocumentOut) => void
  onPublish: (doc: DocumentOut) => void
}) {
  return (
    <li className="group/item grid grid-cols-[1fr_auto] items-center gap-3 border-b border-border px-4 py-3 transition-colors last:border-0 hover:bg-surface-2/50 sm:grid-cols-[minmax(0,1fr)_6rem_7rem_2.5rem]">
      <div className="min-w-0">
        <p className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium text-text">{doc.title}</span>
          {doc.deleted_at ? (
            <Badge tone="danger">已删除</Badge>
          ) : (
            <Badge tone={doc.version_state === 'published' ? 'success' : 'warning'}>
              {VERSION_LABELS[doc.version_state] ?? doc.version_state}
            </Badge>
          )}
        </p>
        <p className="mt-0.5 truncate text-xs text-text-faint">
          {doc.source ? `来源：${doc.source} · ` : ''}
          {formatDateTime(doc.created_at)}
        </p>
      </div>
      <div className="hidden sm:block">
        <Badge tone="neutral">{doc.chunk_count} 分块</Badge>
      </div>
      <p className="hidden text-xs text-text-faint sm:block">{timeAgo(doc.updated_at)}</p>
      <div className={cn('flex justify-end gap-1', !canDelete && !canPublish && 'invisible')}>
        {canPublish && !doc.deleted_at && ['draft', 'scheduled', 'replaced'].includes(doc.version_state) && (
          <button
            type="button"
            onClick={() => onPublish(doc)}
            className="rounded-md px-2 text-xs text-primary hover:bg-primary/10"
          >
            发布
          </button>
        )}
        <button
          type="button"
          onClick={() => onDelete(doc)}
          aria-label={`删除文档 ${doc.title}`}
          className="grid size-9 place-items-center rounded-md text-text-faint opacity-0 transition-all hover:bg-danger/15 hover:text-danger focus-visible:opacity-100 group-hover/item:opacity-100"
        >
          <Trash2 className="size-3.5" aria-hidden />
        </button>
      </div>
    </li>
  )
}

export default function KnowledgePage() {
  const toast = useToast()
  const role = useAuthStore((s) => s.user?.role)
  const userTenantId = useAuthStore((s) => s.user?.tenant_id)
  const canWrite = can(role, 'knowledge_bases', 'write')
  const canDelete = can(role, 'knowledge_bases', 'delete')
  const canSeeDeleted = role === 'system_admin' || role === 'tenant_admin'

  const [ingestOpen, setIngestOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<DocumentOut | null>(null)
  const [showDeleted, setShowDeleted] = useState(false)
  const [versionState, setVersionState] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const documents = useDocuments(canSeeDeleted && showDeleted, canSeeDeleted ? versionState : '')
  const upload = useUploadDocument()
  const remove = useDeleteDocument()
  const publish = usePublishDocument()

  const docs = useMemo(
    () => [...(documents.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [documents.data],
  )

  async function handleFile(file: File) {
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (ext !== 'txt' && ext !== 'md') {
      toast.error('不支持的文件类型', '后端仅支持 .txt 与 .md 的 UTF-8 文本文件')
      return
    }
    try {
      const doc = await upload.mutateAsync(file)
      toast.success('上传成功', `《${doc.title}》切分为 ${doc.chunk_count} 个分块`)
    } catch (err) {
      if (isSessionExpiredError(err)) return
      toast.error('上传失败', err instanceof ApiError ? err.detail : undefined)
    } finally {
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return
    try {
      await remove.mutateAsync({
        id: pendingDelete.id,
        confirmationId:
          userTenantId && pendingDelete.tenant_id !== userTenantId
            ? await createConfirmation(pendingDelete.id, 'delete')
            : undefined,
      })
      toast.success('文档已删除')
    } catch (err) {
      if (isSessionExpiredError(err)) return
      toast.error('删除失败', err instanceof ApiError ? err.detail : undefined)
    } finally {
      setPendingDelete(null)
    }
  }

  async function handlePublish(doc: DocumentOut) {
    try {
      await publish.mutateAsync({
        id: doc.id,
        confirmationId:
          userTenantId && doc.tenant_id !== userTenantId
            ? await createConfirmation(doc.id, 'publish')
            : undefined,
      })
      toast.success('已发布为当前版')
    } catch (err) {
      if (isSessionExpiredError(err)) return
      toast.error('发布失败', err instanceof ApiError ? err.detail : undefined)
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="知识库"
        description="摄取文本或上传文档后，对话会按混合检索（向量 + BM25 + RRF 融合）命中相关内容。"
        actions={
          canWrite ? (
            <>
              <input
                ref={fileRef}
                type="file"
                accept=".txt,.md,text/plain,text/markdown"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) void handleFile(file)
                }}
              />
              <Button
                variant="secondary"
                loading={upload.isPending}
                onClick={() => fileRef.current?.click()}
                icon={<FileUp className="size-4" aria-hidden />}
              >
                上传文件
              </Button>
              <Button variant="primary" onClick={() => setIngestOpen(true)} icon={<Plus className="size-4" aria-hidden />}>
                摄取文本
              </Button>
            </>
          ) : undefined
        }
      />

      <SearchPanel />

      <section className="panel overflow-hidden">
        <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <h2 className="font-display text-sm font-semibold text-text">文档</h2>
          <div className="flex items-center gap-3">
            {canSeeDeleted && (
              <select
                value={versionState}
                onChange={(event) => setVersionState(event.target.value)}
                className="rounded-md border border-border bg-transparent px-2 py-1 text-xs text-text-muted"
                aria-label="按状态筛选"
              >
                <option value="">全部状态</option>
                <option value="draft">草稿</option>
                <option value="scheduled">待生效</option>
                <option value="published">已发布</option>
                <option value="replaced">已替换</option>
                <option value="archived">已归档</option>
              </select>
            )}
            {canSeeDeleted && (
              <button
                type="button"
                onClick={() => setShowDeleted((value) => !value)}
                className="text-xs text-text-muted hover:text-text"
              >
                {showDeleted ? '隐藏已删除' : '显示已删除'}
              </button>
            )}
            <span className="text-xs text-text-faint">共 {docs.length} 篇</span>
          </div>
        </header>

        {documents.isLoading ? (
          <SkeletonRows rows={5} className="p-4" />
        ) : documents.error ? (
          <ErrorState error={documents.error} onRetry={() => void documents.refetch()} />
        ) : docs.length === 0 ? (
          <EmptyState
            icon={<FileText className="size-5" aria-hidden />}
            title="知识库还是空的"
            description="摄取一段文本或上传 .txt / .md 文件，助手就能引用这些内容回答问题。"
            action={
              canWrite ? (
                <Button variant="primary" size="sm" onClick={() => setIngestOpen(true)}>
                  摄取第一段文本
                </Button>
              ) : undefined
            }
          />
        ) : (
          <ul>
            {docs.map((doc) => (
              <DocumentRow
                key={doc.id}
                doc={doc}
                canDelete={canDelete}
                canPublish={canSeeDeleted}
                onDelete={setPendingDelete}
                onPublish={(item) => void handlePublish(item)}
              />
            ))}
          </ul>
        )}
      </section>

      <IngestModal open={ingestOpen} onClose={() => setIngestOpen(false)} />

      <Modal
        open={Boolean(pendingDelete)}
        onClose={() => setPendingDelete(null)}
        title="删除文档"
        description="文档及其全部分块、向量将从知识库中移除，此操作不可撤销。"
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
        <p className="text-sm text-text-muted">{pendingDelete?.title}</p>
      </Modal>
    </div>
  )
}
