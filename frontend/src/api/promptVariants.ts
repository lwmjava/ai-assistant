import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import { useAuthStore } from '@/store/auth'

export type PromptSlot = {
  key: string
  title: string
  source_fingerprint: string
  published_variant_id: string | null
  publication_revision: number | null
}

export type PromptVariant = {
  id: string
  slot_key: string
  label: string
  body: string
  body_fingerprint: string
  status: string
  created_by: string
  notes: string | null
  created_at: string
  updated_at: string
}

export type PromptCompare = {
  id: string
  slot_key: string
  baseline_variant_id: string
  candidate_variant_id: string
  status: string
  provider: string
  binding: Record<string, unknown>
  baseline_metrics: Record<string, unknown>
  candidate_metrics: Record<string, unknown>
  missing_metrics: string[]
  diff: Record<string, unknown>
  cost: Record<string, unknown>
  call_stats: Record<string, unknown>
  auto_publish_blocked: boolean
  external_calls_used: number
  max_external_calls: number
  created_at: string
}

export type PromptPublication = {
  slot_key: string
  active_variant_id: string
  previous_variant_id: string | null
  revision: number
  published_by: string
  published_at: string
  note: string | null
}

const base = '/evaluations/prompt-variants'

export function usePromptSlots(enabled: boolean) {
  const user = useAuthStore((s) => s.user)
  return useQuery({
    queryKey: ['prompt-slots', user?.id, user?.tenant_id, user?.role],
    queryFn: () => api.get<PromptSlot[]>(${base}/slots),
    enabled,
  })
}

export function usePromptVariants(slotKey: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ['prompt-variants', slotKey],
    queryFn: () => api.get<PromptVariant[]>(${base}/slots//variants),
    enabled: enabled && slotKey !== null,
  })
}

export function usePromptPublication(slotKey: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ['prompt-publication', slotKey],
    queryFn: () => api.get<PromptPublication | null>(${base}/slots//publication),
    enabled: enabled && slotKey !== null,
  })
}

export function useCreatePromptVariant() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: { slotKey: string; label: string; body: string; notes?: string }) =>
      api.post<PromptVariant>(${base}/slots//variants, {
        label: input.label,
        body: input.body,
        notes: input.notes,
      }),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ['prompt-variants', vars.slotKey] })
    },
  })
}

export function useCreatePromptCompare() {
  return useMutation({
    mutationFn: (input: {
      slot_key: string
      baseline_variant_id: string
      candidate_variant_id: string
      dataset_id: string
      dataset_version_id: string
    }) =>
      api.post<PromptCompare>(${base}/compares, {
        provider: 'fake',
        max_external_calls: 0,
        ...input,
      }),
  })
}

export function usePublishPromptVariant() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      slotKey: string
      variant_id: string
      expected_revision: number
      note?: string
      compare_run_id?: string
    }) =>
      api.post<PromptPublication>(${base}/slots//publish, {
        variant_id: input.variant_id,
        expected_revision: input.expected_revision,
        note: input.note,
        compare_run_id: input.compare_run_id,
      }),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ['prompt-publication', vars.slotKey] })
      void qc.invalidateQueries({ queryKey: ['prompt-slots'] })
    },
  })
}

export function useRollbackPromptVariant() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      slotKey: string
      expected_revision: number
      to_variant_id?: string
      note?: string
    }) =>
      api.post<PromptPublication>(${base}/slots//rollback, {
        expected_revision: input.expected_revision,
        to_variant_id: input.to_variant_id,
        note: input.note,
      }),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ['prompt-publication', vars.slotKey] })
      void qc.invalidateQueries({ queryKey: ['prompt-slots'] })
    },
  })
}
