import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '@/lib/http'

type Scope = 'private' | 'tenant' | 'system'

interface Definition {
  id: string
  name: string
  scope: Scope
  active_version: number | null
  revision: number
  enabled: boolean
}

const path = '/chat/agent-definitions'
const queryKey = ['agent-definitions'] as const

export default function AgentDefinitions() {
  const client = useQueryClient()
  const definitions = useQuery({ queryKey, queryFn: () => api.get<Definition[]>(path) })
  const change = useMutation({
    mutationFn: (operation: () => Promise<Definition>) => operation(),
    onSuccess: () => client.invalidateQueries({ queryKey }),
  })
  const [name, setName] = useState('')
  const [scope, setScope] = useState<Scope>('private')
  const [selectedId, setSelectedId] = useState('')
  const [instructions, setInstructions] = useState('')
  const [tools, setTools] = useState('')
  const [version, setVersion] = useState('')
  const selected = definitions.data?.find((definition) => definition.id === selectedId)

  return (
    <section className="space-y-4 p-6">
      <h1 className="text-xl font-semibold">Agent 定义</h1>
      {definitions.isPending && <p role="status">正在加载</p>}
      {definitions.isError && <p role="alert">加载 Agent 定义失败</p>}
      {change.isError && <p role="alert">操作失败，请检查权限、工具名称或当前版本后重试。</p>}
      <form className="flex flex-wrap gap-3" onSubmit={(event) => {
        event.preventDefault()
        change.mutate(() => api.post<Definition>(path, { name, scope }))
      }}>
        <label>名称 <input required maxLength={120} value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>作用域 <select value={scope} onChange={(event) => setScope(event.target.value as Scope)}>
          <option value="private">私有</option>
          <option value="tenant">租户</option>
          <option value="system">系统</option>
        </select></label>
        <button type="submit" disabled={change.isPending}>创建</button>
      </form>
      <label>选择定义 <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
        <option value="">请选择</option>
        {definitions.data?.map((definition) => (
          <option key={definition.id} value={definition.id}>{definition.name} · {definition.scope}</option>
        ))}
      </select></label>
      {selected && <div key={selected.id} className="space-y-3">
        <p>{selected.enabled ? '已启用' : '已停用'} · 生效版本 {selected.active_version ?? '无'}</p>
        <button type="button" disabled={change.isPending || selected.active_version === null} onClick={() => {
          change.mutate(() => api.put<Definition>(`${path}/${selected.id}/enabled`, {
            expected_revision: selected.revision,
            enabled: !selected.enabled,
          }))
        }}>{selected.enabled ? '停用' : '启用'}</button>
        <form className="space-y-2" onSubmit={(event) => {
          event.preventDefault()
          change.mutate(() => api.post<Definition>(`${path}/${selected.id}/publish`, {
            expected_revision: selected.revision,
            instructions,
            tool_names: tools.split(',').map((tool) => tool.trim()).filter(Boolean),
          }))
        }}>
          <label className="block">版本指令 <textarea required maxLength={16000} value={instructions}
            onChange={(event) => setInstructions(event.target.value)} /></label>
          <label>工具名称（逗号分隔） <input value={tools} onChange={(event) => setTools(event.target.value)} /></label>
          <button type="submit" disabled={change.isPending}>发布版本</button>
        </form>
        <form onSubmit={(event) => {
          event.preventDefault()
          change.mutate(() => api.post<Definition>(`${path}/${selected.id}/rollback`, {
            expected_revision: selected.revision,
            version: Number(version),
          }))
        }}>
          <label>历史版本 <input required type="number" min={1} value={version}
            onChange={(event) => setVersion(event.target.value)} /></label>
          <button type="submit" disabled={change.isPending}>回滚</button>
        </form>
      </div>}
    </section>
  )
}
