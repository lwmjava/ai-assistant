/** MCP 服务器与工具查询。 */

import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/http'
import type { McpServerInfo, ToolItem } from '@/types/api'

/** 已配置的 MCP 服务器（不含密钥字段）。 */
export function useMcpServers(enabled = true) {
  return useQuery({
    queryKey: ['mcp', 'servers'],
    queryFn: () => api.get<McpServerInfo[]>('/mcp/servers'),
    enabled,
    staleTime: 60_000,
    // MCP 未启用时后端返回 503，交由页面展示「未启用」态而非报错
    retry: false,
  })
}

/** 已连接 MCP 服务器暴露的工具；未启用或未连接时后端返回 503。 */
export function useMcpTools(enabled = true) {
  return useQuery({
    queryKey: ['mcp', 'tools'],
    queryFn: () => api.get<ToolItem[]>('/mcp/tools'),
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}
