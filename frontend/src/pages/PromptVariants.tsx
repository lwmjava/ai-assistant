import { useMemo, useState } from 'react'

import {
  useCreatePromptCompare,
  useCreatePromptVariant,
  usePromptPublication,
  usePromptSlots,
  usePromptVariants,
  usePublishPromptVariant,
  useRollbackPromptVariant,
  type PromptCompare,
} from '@/api/promptVariants'
import { useAuthStore } from '@/store/auth'

export default function PromptVariantsPage() {
  const user = useAuthStore((s) => s.user)
  const canPublish = user?.role === 'system_admin'
  const slotsQuery = usePromptSlots(true)
  const [slotKey, setSlotKey] = useState<string>('')
  const effectiveSlot = slotKey || slotsQuery.data?.[0]?.key || null
  const variantsQuery = usePromptVariants(effectiveSlot, effectiveSlot !== null)
  const publicationQuery = usePromptPublication(effectiveSlot, effectiveSlot !== null)
  const createVariant = useCreatePromptVariant()
  const compareMut = useCreatePromptCompare()
  const publishMut = usePublishPromptVariant()
  const rollbackMut = useRollbackPromptVariant()

  const [label, setLabel] = useState('candidate')
  const [body, setBody] = useState('')
  const [baselineId, setBaselineId] = useState('')
  const [candidateId, setCandidateId] = useState('')
  const [datasetId, setDatasetId] = useState('')
  const [datasetVersionId, setDatasetVersionId] = useState('')
  const [compare, setCompare] = useState<PromptCompare | null>(null)
  const [error, setError] = useState<string | null>(null)

  const variants = variantsQuery.data ?? []
  const publication = publicationQuery.data ?? null
  const selectedSlot = useMemo(
    () => slotsQuery.data?.find((item) => item.key === effectiveSlot) ?? null,
    [slotsQuery.data, effectiveSlot],
  )

  async function onCreateVariant() {
    if (!effectiveSlot) return
    setError(null)
    try {
      await createVariant.mutateAsync({ slotKey: effectiveSlot, label, body })
      setBody('')
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败')
    }
  }

  async function onCompare() {
    if (!effectiveSlot) return
    setError(null)
    try {
      const result = await compareMut.mutateAsync({
        slot_key: effectiveSlot,
        baseline_variant_id: baselineId,
        candidate_variant_id: candidateId,
        dataset_id: datasetId,
        dataset_version_id: datasetVersionId,
      })
      setCompare(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : '比较失败')
    }
  }

  async function onPublish() {
    if (!effectiveSlot || !candidateId) return
    setError(null)
    try {
      await publishMut.mutateAsync({
        slotKey: effectiveSlot,
        variant_id: candidateId,
        expected_revision: publication?.revision ?? selectedSlot?.publication_revision ?? 0,
        compare_run_id: compare?.id,
        note: 'manual publish',
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : '发布失败')
    }
  }

  async function onRollback() {
    if (!effectiveSlot || publication == null) return
    setError(null)
    try {
      await rollbackMut.mutateAsync({
        slotKey: effectiveSlot,
        expected_revision: publication.revision,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : '回滚失败')
    }
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 p-6">
      <header>
        <h1 className="text-2xl font-semibold">Prompt 变体比较与发布</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          离线 fake 比较会绑定数据集、源码、模型与 Prompt 指纹；缺测指标不会记成 0。胜出变体不会自动发布。
        </p>
      </header>

      {error ? <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</p> : null}

      <section className="rounded border p-4">
        <h2 className="mb-3 font-medium">槽位</h2>
        <select
          className="w-full rounded border px-3 py-2"
          value={effectiveSlot ?? ''}
          onChange={(e) => setSlotKey(e.target.value)}
        >
          {(slotsQuery.data ?? []).map((slot) => (
            <option key={slot.key} value={slot.key}>
              {slot.title} ({slot.key})
            </option>
          ))}
        </select>
        {selectedSlot ? (
          <p className="mt-2 text-xs text-muted-foreground">
            源指纹 {selectedSlot.source_fingerprint.slice(0, 12)}…；已发布{' '}
            {selectedSlot.published_variant_id ?? '无'}；revision{' '}
            {selectedSlot.publication_revision ?? 0}
          </p>
        ) : null}
      </section>

      <section className="rounded border p-4">
        <h2 className="mb-3 font-medium">新建草稿变体</h2>
        <div className="flex flex-col gap-2">
          <input
            className="rounded border px-3 py-2"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="标签"
          />
          <textarea
            className="min-h-28 rounded border px-3 py-2"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Prompt 正文"
          />
          <button type="button" className="self-start rounded bg-slate-900 px-3 py-2 text-white" onClick={() => void onCreateVariant()}>
            保存草稿
          </button>
        </div>
        <ul className="mt-4 space-y-2 text-sm">
          {variants.map((item) => (
            <li key={item.id} className="rounded border px-3 py-2">
              <div className="font-medium">{item.label}</div>
              <div className="text-xs text-muted-foreground">{item.id} · {item.body_fingerprint.slice(0, 12)}…</div>
            </li>
          ))}
        </ul>
      </section>

      <section className="rounded border p-4">
        <h2 className="mb-3 font-medium">离线比较（provider=fake）</h2>
        <div className="grid gap-2 md:grid-cols-2">
          <input className="rounded border px-3 py-2" placeholder="baseline variant id" value={baselineId} onChange={(e) => setBaselineId(e.target.value)} />
          <input className="rounded border px-3 py-2" placeholder="candidate variant id" value={candidateId} onChange={(e) => setCandidateId(e.target.value)} />
          <input className="rounded border px-3 py-2" placeholder="dataset id" value={datasetId} onChange={(e) => setDatasetId(e.target.value)} />
          <input className="rounded border px-3 py-2" placeholder="dataset version id" value={datasetVersionId} onChange={(e) => setDatasetVersionId(e.target.value)} />
        </div>
        <button type="button" className="mt-3 rounded bg-slate-900 px-3 py-2 text-white" onClick={() => void onCompare()}>
          运行 fake 比较
        </button>
        {compare ? (
          <pre className="mt-3 overflow-auto rounded bg-slate-50 p-3 text-xs">
{JSON.stringify(
  {
    status: compare.status,
    binding: compare.binding,
    missing_metrics: compare.missing_metrics,
    diff: compare.diff,
    cost: compare.cost,
    call_stats: compare.call_stats,
    auto_publish_blocked: compare.auto_publish_blocked,
  },
  null,
  2,
)}
          </pre>
        ) : null}
      </section>

      <section className="rounded border p-4">
        <h2 className="mb-3 font-medium">人工发布 / 回滚</h2>
        <p className="mb-3 text-sm text-muted-foreground">
          当前发布：{publication ? `${publication.active_variant_id} @ rev ${publication.revision}` : '尚未发布'}
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={!canPublish}
            className="rounded bg-emerald-700 px-3 py-2 text-white disabled:opacity-50"
            onClick={() => void onPublish()}
          >
            发布候选（需 system_admin）
          </button>
          <button
            type="button"
            disabled={!canPublish || publication == null}
            className="rounded bg-amber-700 px-3 py-2 text-white disabled:opacity-50"
            onClick={() => void onRollback()}
          >
            回滚到 previous
          </button>
        </div>
      </section>
    </div>
  )
}
