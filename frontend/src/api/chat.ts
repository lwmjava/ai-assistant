/** 对话相关查询与变更。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type {
  ChatRequest,
  ChatResponse,
  ConversationDetail,
  ConversationOut,
  ToolItem,
} from '@/types/api'

export const conversationKeys = {
  all: ['conversations'] as const,
  list: () => [...conversationKeys.all, 'list'] as const,
  detail: (id: string) => [...conversationKeys.all, 'detail', id] as const,
}

/** 会话列表（当前用户可见范围由后端按租户/归属过滤）。 */
export function useConversations() {
  return useQuery({
    queryKey: conversationKeys.list(),
    queryFn: () => api.get<ConversationOut[]>('/chat/conversations'),
    staleTime: 10_000,
  })
}

/** 会话详情（含消息列表）。 */
export function useConversation(id: string | null) {
  return useQuery({
    queryKey: conversationKeys.detail(id ?? ''),
    queryFn: () => api.get<ConversationDetail>(`/chat/conversations/${id}`),
    enabled: Boolean(id),
  })
}

export function useDeleteConversation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.delete<{ deleted: boolean }>(`/chat/conversations/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: conversationKeys.all })
    },
  })
}

/**
 * 非流式对话（POST /chat）。
 *
 * 与 `/chat/stream` 走同一条 Agent 管线，差别只在返回方式：一次性拿到完整
 * `reply`，并且**回传 `conversation_id`** —— 新建会话时可以直接定位，
 * 不必像流式那样靠列表按 `updated_at` 排序去猜。代价是等待期间看不到
 * 管线阶段与增量文本。
 *
 * 这里用普通函数而非 `useMutation`：`mutateAsync` 不转发 `AbortSignal`，
 * 而一次性响应可能耗时长达数十秒，必须支持用户中断。
 *
 * @param signal 中断信号；中断会抛 `AbortError`（DOMException），调用方需与真实错误区分。
 */
export function sendMessage(body: ChatRequest, signal?: AbortSignal) {
  return api.post<ChatResponse>('/chat', body, { signal })
}

/** Agent 可用工具清单（内置 + MCP，需 agents:read）。 */
export function useChatTools(enabled = true) {
  return useQuery({
    queryKey: ['chat', 'tools'],
    queryFn: () => api.get<ToolItem[]>('/chat/tools'),
    enabled,
    staleTime: 60_000,
  })
}
