/** 当前用户自己的跨会话记忆。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'

export interface MemorySwitch {
  deployment_enabled: boolean
  user_enabled: boolean
  effective: boolean
}

export interface MemoryItem {
  id: string
  source_grade: string
  content: string
  source_conversation_id: string
  source_message_id: string
  supersedes_id: string | null
  conflict_group_id: string | null
  invalidated: boolean
}

export interface MemoryExportItem {
  id: string
  source_grade: string
  content: string
  source_conversation_id: string
  source_message_id: string
  supersedes_id: string | null
  superseded: boolean
  conflict_group_id: string | null
}

const keys = {
  all: ['memories'] as const,
  settings: ['memories', 'settings'] as const,
}

export function useMemorySwitch() {
  return useQuery({
    queryKey: keys.settings,
    queryFn: () => api.get<MemorySwitch>('/memories/settings'),
  })
}

export function useMemories() {
  return useQuery({
    queryKey: keys.all,
    queryFn: () => api.get<MemoryItem[]>('/memories'),
  })
}

export function useUpdateMemorySwitch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (enabled: boolean) => api.put<MemorySwitch>('/memories/settings', { enabled }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.all })
    },
  })
}

export function useCorrectMemory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: { id: string; content: string }) =>
      api.post<MemoryItem>(`/memories/${encodeURIComponent(input.id)}/corrections`, {
        content: input.content,
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.all })
    },
  })
}

export function useInvalidateMemory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.delete<MemoryItem>(`/memories/${encodeURIComponent(id)}`),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.all })
    },
  })
}

export async function downloadMemoryExport(): Promise<void> {
  const rows = await api.get<MemoryExportItem[]>('/memories/export')
  const blob = new Blob([JSON.stringify(rows, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'memories.json'
  link.click()
  URL.revokeObjectURL(url)
}
