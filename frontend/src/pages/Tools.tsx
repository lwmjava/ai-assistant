/** 工具与 MCP 页：Agent 可用工具清单、MCP 服务器配置与暴露的工具。 */

import { useState } from 'react'
import { Braces, ChevronDown, Plug, Power, Wrench } from 'lucide-react'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ui/Feedback'
import { useMcpServers, useMcpTools } from '@/api/agents'
import { useChatTools } from '@/api/chat'
import { cn, truncate } from '@/lib/cn'
import type { McpServerInfo, ToolItem } from '@/types/api'

function ToolCard({ tool }: { tool: ToolItem }) {
  const [expanded, setExpanded] = useState(false)
  const params = tool.parameters as { properties?: Record<string, { description?: string; type?: string }> }
  const props = Object.entries(params?.properties ?? {})

  return (
    <li className="panel-inset overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2/40"
      >
        <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg border border-primary/25 bg-primary/10 text-primary">
          <Wrench className="size-4" aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-mono text-sm font-medium text-text">{tool.name}</span>
          <span className="mt-0.5 block text-sm text-text-muted">{tool.description}</span>
        </span>
        <ChevronDown
          className={cn('mt-1 size-4 shrink-0 text-text-faint transition-transform', expanded && 'rotate-180')}
          aria-hidden
        />
      </button>

      {expanded && (
        <div className="border-t border-border bg-surface-2/40 px-4 py-3">
          {props.length === 0 ? (
            <p className="text-sm text-text-faint">该工具无需参数。</p>
          ) : (
            <ul className="space-y-2">
              {props.map(([name, schema]) => (
                <li key={name} className="flex flex-wrap items-baseline gap-2">
                  <code className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-xs text-text">
                    {name}
                  </code>
                  {schema.type && <Badge tone="neutral">{schema.type}</Badge>}
                  {schema.description && (
                    <span className="text-sm text-text-muted">{schema.description}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  )
}

function ToolSection({
  title,
  description,
  tools,
  loading,
  error,
  onRetry,
  emptyHint,
  disabledHint,
}: {
  title: string
  description?: string
  tools: ToolItem[] | undefined
  loading: boolean
  error: unknown
  onRetry: () => void
  emptyHint: string
  disabledHint: string
}) {
  return (
    <section className="panel overflow-hidden">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <div>
          <h2 className="font-display text-sm font-semibold text-text">{title}</h2>
          {description && <p className="mt-0.5 text-xs text-text-faint">{description}</p>}
        </div>
        {tools && <Badge tone="primary">{tools.length} 个</Badge>}
      </header>

      <div className="p-3">
        {loading ? (
          <SkeletonRows rows={3} className="p-1" />
        ) : error ? (
          <ErrorState
            error={error}
            onRetry={onRetry}
            className="border-0 bg-transparent py-6"
            // 503 时 ErrorState 已给出「模块未启用」文案，这里补充具体开关名
            title={(error as { status?: number })?.status === 503 ? disabledHint : undefined}
          />
        ) : !tools || tools.length === 0 ? (
          <EmptyState title={emptyHint} className="py-8" />
        ) : (
          <ul className="space-y-2">
            {tools.map((tool) => (
              <ToolCard key={tool.name} tool={tool} />
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

function ServerList({ servers }: { servers: McpServerInfo[] }) {
  if (servers.length === 0) {
    return (
      <EmptyState
        icon={<Plug className="size-5" aria-hidden />}
        title="尚未配置 MCP 服务器"
        description="在后端 MCP_SERVERS 中登记服务器后，其工具会自动进入 Agent 工具箱。"
        className="py-8"
      />
    )
  }
  return (
    <ul className="space-y-2">
      {servers.map((s) => (
        <li key={String(s.name)} className="panel-inset flex flex-wrap items-center gap-3 px-4 py-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-accent/30 bg-accent/10 text-accent">
            <Plug className="size-4" aria-hidden />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-mono text-sm text-text">{String(s.name)}</span>
            {s.transport && <span className="text-xs text-text-faint">传输：{String(s.transport)}</span>}
          </span>
          <Badge tone={s.enabled === false ? 'danger' : 'success'} dot pulse={s.enabled !== false}>
            {s.enabled === false ? '未启用' : '已启用'}
          </Badge>
        </li>
      ))}
    </ul>
  )
}

export default function ToolsPage() {
  const agentTools = useChatTools()
  const mcpServers = useMcpServers()
  const mcpTools = useMcpTools()
  const mcpEnabled = (mcpServers.error as { status?: number } | null)?.status !== 503

  return (
    <div className="space-y-5">
      <PageHeader
        title="工具与 MCP"
        description="Agent 在「行动」阶段按提示词约定的 &lt;tool_call&gt; 信封调用工具；MCP 协议用于接入企业既有系统。"
      />

      <div className="grid gap-5 xl:grid-cols-2">
        <ToolSection
          title="Agent 可用工具"
          description="内置工具与已连接 MCP 服务器的并集"
          tools={agentTools.data}
          loading={agentTools.isLoading}
          error={agentTools.error}
          onRetry={() => void agentTools.refetch()}
          emptyHint="暂无可用工具"
          disabledHint="暂无可用工具"
        />

        <ToolSection
          title="MCP 服务器工具"
          description="由已连接的 MCP 服务器暴露"
          tools={mcpTools.data}
          loading={mcpTools.isLoading}
          error={mcpTools.error}
          onRetry={() => void mcpTools.refetch()}
          emptyHint="没有可用的 MCP 工具"
          disabledHint="MCP 未启用或未连接任何服务器"
        />
      </div>

      <section className="panel overflow-hidden">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
          <div>
            <h2 className="font-display text-sm font-semibold text-text">已配置的 MCP 服务器</h2>
            <p className="mt-0.5 text-xs text-text-faint">
              密钥类字段由后端过滤，不会出现在该列表中
            </p>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              void mcpServers.refetch()
              void mcpTools.refetch()
            }}
            icon={<Power className="size-3.5" aria-hidden />}
          >
            刷新
          </Button>
        </header>

        <div className="p-3">
          {mcpServers.isLoading ? (
            <SkeletonRows rows={2} className="p-1" />
          ) : mcpServers.error ? (
            <ErrorState
              error={mcpServers.error}
              onRetry={() => void mcpServers.refetch()}
              className="border-0 bg-transparent py-6"
              title={
                (mcpServers.error as { status?: number })?.status === 503
                  ? 'MCP 未启用'
                  : undefined
              }
            />
          ) : (
            <ServerList servers={mcpServers.data ?? []} />
          )}
        </div>
      </section>

      {mcpEnabled && (mcpTools.data?.length ?? 0) === 0 && (
        <p className="mx-auto max-w-2xl text-center text-xs text-text-faint">
          提示：MCP 工具箱为空时，Agent 仍可使用内置的计算器、时间与网页抓取工具。
        </p>
      )}

      <section className="panel grain relative overflow-hidden p-4">
        <div className="relative flex items-start gap-3">
          <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg border border-border bg-surface-2 text-text-muted">
            <Braces className="size-4" aria-hidden />
          </span>
          <div className="space-y-1">
            <h3 className="text-sm font-medium text-text">工具调用信封格式</h3>
            <p className="text-sm text-text-muted">
              模型通过 <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs">&lt;tool_call&gt;</code>{' '}
              标签输出 JSON 调用请求，由运行时解析、执行并把结果回填上下文，
              最多循环 <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs">AGENT_MAX_TOOL_ROUNDS</code> 轮。
            </p>
          </div>
        </div>
      </section>

      <p className="text-center text-xs text-text-faint">
        {truncate('工具清单属于服务端能力信息，需 agents:read 权限才可查看。', 120)}
      </p>
    </div>
  )
}
