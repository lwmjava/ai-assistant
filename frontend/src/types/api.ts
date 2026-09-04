/** 后端 API 契约类型定义，字段与 FastAPI 的 Pydantic 模型一一对应。 */

export type Role =
  | 'system_admin'
  | 'system_viewer'
  | 'tenant_admin'
  | 'member'
  | 'viewer'

/** 五级角色的中文标签与配色档位，用于侧边栏与徽章。 */
export const ROLE_META: Record<Role, { label: string; tone: 'primary' | 'accent' | 'neutral' }> = {
  system_admin: { label: '平台管理员', tone: 'primary' },
  system_viewer: { label: '平台审计员', tone: 'accent' },
  tenant_admin: { label: '租户管理员', tone: 'primary' },
  member: { label: '成员', tone: 'neutral' },
  viewer: { label: '访客', tone: 'neutral' },
}

export interface Token {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface UserInfo {
  id: string
  tenant_id: string
  username: string
  email: string | null
  role: Role
  is_active: boolean
}

export interface LoginRequest {
  username: string
  password: string
}

// ── 对话 ─────────────────────────────────────────────
export interface ConversationOut {
  id: string
  tenant_id: string
  user_id: string
  title: string | null
  created_at: string
  updated_at: string
}

export interface MessageOut {
  id: string
  role: string
  content: string
  model: string | null
  created_at: string
}

export interface ConversationDetail extends ConversationOut {
  messages: MessageOut[]
}

export interface ChatRequest {
  message: string
  conversation_id: string | null
}

export interface ChatResponse {
  conversation_id: string
  reply: string
  model: string | null
}

/**
 * 发送模式：`stream` 走 SSE 增量返回并附管线阶段；`once` 走普通 POST，
 * 等待完整结果一次性落地。**只有 `once` 会回传 `conversation_id`**，
 * 新建会话时无需靠列表排序去猜。
 */
export type SendMode = 'stream' | 'once'

/** 流式事件类型：stage=管线阶段、token=增量文本、tool=工具调用、done=完成、error=失败。 */
export type StreamEventType = 'stage' | 'token' | 'tool' | 'done' | 'error'

export interface StreamEvent {
  type: StreamEventType
  data: string
}

/**
 * 管线阶段的规范顺序，与 `app/agents/pipeline.py` 中 yield 的先后顺序一致。
 *
 * 「检索」仅在 RAG 启用时出现，「意图分流」与「质量门自纠错」分别由
 * 意图路由与质量门开关决定是否出现——未出现的阶段直接不展示，
 * 但保留其位置以保证剩余阶段的相对顺序正确。
 */
export const PIPELINE_STAGES = [
  '理解',
  '意图分流',
  '规划',
  '检索',
  '行动',
  '质量门自纠错',
  '反思',
  '响应',
] as const

/** 简要序列：用于登录页与说明文案，省略条件分支阶段。 */
export const PIPELINE_STAGES_BRIEF = ['理解', '规划', '检索', '行动', '反思', '响应'] as const

// ── 知识库 ───────────────────────────────────────────
export interface DocumentOut {
  id: string
  tenant_id: string
  user_id: string
  title: string
  source: string | null
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface SearchResultOut {
  document_id: string
  content: string
  source: string | null
  score: number
}

// ── 工具与 MCP ───────────────────────────────────────
export interface ToolItem {
  name: string
  description: string
  parameters: Record<string, unknown>
}

export interface McpServerInfo {
  name: string
  transport?: string
  enabled?: boolean
  [key: string]: unknown
}

// ── 工作流 ───────────────────────────────────────────
export interface WorkflowOut {
  id: string
  tenant_id: string
  owner_id: string
  name: string
  description: string | null
  cron_expr: string
  prompt_template: string
  timezone: string
  enabled: boolean
  suspended_owner: boolean
  webhook_url: string | null
  created_at: string
  updated_at: string
}

export interface ExecutionOut {
  id: string
  workflow_id: string
  tenant_id: string
  triggered_by: string
  status: string
  input: string | null
  output: string | null
  error: string | null
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  created_at: string
  updated_at: string
}

export interface WorkflowCreate {
  name: string
  cron_expr: string
  prompt_template: string
  description?: string | null
  timezone?: string | null
  webhook_url?: string | null
  enabled?: boolean
}

export type WorkflowUpdate = Partial<WorkflowCreate>

// ── 审计 ────────────────────────────────────────────
export interface AuditLogOut {
  id: string
  user_id: string | null
  tenant_id: string | null
  action: string
  resource_type: string | null
  resource_id: string | null
  details: string | null
  ip_address: string | null
  user_agent: string | null
  created_at: string
}

export interface AuditLogPage {
  items: AuditLogOut[]
  total: number
  page: number
  page_size: number
}

export interface HealthInfo {
  status: string
  app: string
  version: string
  env: string
}
