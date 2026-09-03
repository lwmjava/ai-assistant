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
import { useDeleteDocument, useDocuments, useIngestDocument, useSearch, useUploadDocument } from '@/api/rag'
import { ApiError } from '@/lib/http'
import { can } from '@/lib/permissions'
import { cn, formatDateTime, timeAgo } from '@/lib/cn'
import { useAuthStore } from '@/store/auth'
import type { DocumentOut, SearchResultOut } from '@/types/api'

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

        {search.error && (
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
  onDelete,
}: {
  doc: DocumentOut
  canDelete: boolean
  onDelete: (doc: DocumentOut) => void
}) {
  return (
    <li className="group/item grid grid-cols-[1fr_auto] items-center gap-3 border-b border-border px-4 py-3 transition-colors last:border-0 hover:bg-surface-2/50 sm:grid-cols-[minmax(0,1fr)_6rem_7rem_2.5rem]">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-text">{doc.title}</p>
        <p className="mt-0.5 truncate text-xs text-text-faint">
          {doc.source ? `来源：${doc.source} · ` : ''}
          {formatDateTime(doc.created_at)}
        </p>
      </div>
      <div className="hidden sm:block">
        <Badge tone="neutral">{doc.chunk_count} 分块</Badge>
      </div>
      <p className="hidden text-xs text-text-faint sm:block">{timeAgo(doc.updated_at)}</p>
      <div className={cn('flex justify-end', !canDelete && 'invisible')}>
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
  const canWrite = can(role, 'knowledge_bases', 'write')
  const canDelete = can(role, 'knowledge_bases', 'delete')

  const [ingestOpen, setIngestOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<DocumentOut | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const documents = useDocuments()
  const upload = useUploadDocument()
  const remove = useDeleteDocument()

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
      toast.error('上传失败', err instanceof ApiError ? err.detail : undefined)
    } finally {
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return
    try {
      await remove.mutateAsync(pendingDelete.id)
      toast.success('文档已删除')
    } catch (err) {
      toast.error('删除失败', err instanceof ApiError ? err.detail : undefined)
    } finally {
      setPendingDelete(null)
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
          <span className="text-xs text-text-faint">共 {docs.length} 篇</span>
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
                onDelete={setPendingDelete}
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
