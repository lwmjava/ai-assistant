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
  must_change_password: boolean
  is_test_user: boolean
}

export interface ChangePasswordRequest {
  current_password: string
  new_password: string
}

export interface UserAdminOut extends UserInfo {
  tenant_name: string
}

export interface UserPage {
  items: UserAdminOut[]
  total: number
  page: number
  page_size: number
}

export interface SystemStatus {
  status: string
  app: string
  version: string
  env: string
  started_at: string
  checks: {
    database: { status: string }
    vector_store: { status: string; backend: string }
  }
  online_users: number
  storage_bytes: number
  api_request_count: number
}

export interface LoginRequest {
  username: string
  password: string
}

export interface SetupStatus {
  needs_setup: boolean
}

export interface MembershipOut {
  tenant_id: string
  tenant_name: string
  role: string
}

export interface InvitationOut {
  id: string
  code: string
  tenant_id: string
  role: string
  expires_at: string
  max_uses: number
  use_count: number
}

// ── 对话 ─────────────────────────────────────────────
export interface ConversationOut {
  id: string
  tenant_id: string
  user_id: string
  title: string | null
  created_at: string
  updated_at: string
  deleted_at?: string | null
  archived_at?: string | null
}

export interface SourceRef {
  filename: string
  page: number | null
  section: string | null
  /** 摘录原文片段，用于展示引用内容。 */
  excerpt?: string | null
  /** 分块 id，可定位到具体分块。 */
  chunk_id?: string | null
  /** 所属文档 id，可跳转知识库定位。 */
  document_id?: string | null
}

export interface CodeResult {
  status: 'ok' | 'error' | 'timeout' | string
  stdout: string
  reason: string
}

export interface ChatAttachmentInput {
  name: string
  content_base64: string
}

export interface ChatAttachmentSummary {
  name: string
  size: number
  mime_type: string
}

export interface MessageOut {
  attempt_id?: string | null
  attempt_outcome?: string | null
  feedback_submitted?: boolean
  feedback_id?: string | null
  rating?: 'up' | 'down' | null
  rating_id?: string | null
  id: string
  role: string
  content: string
  model: string | null
  created_at: string
  sources?: SourceRef[]
  code_results?: CodeResult[]
  skill_names?: string[]
  attachments?: ChatAttachmentSummary[]
  /** complete：正常写完。stopped：生成已停下，不是完整回复。 */
  status?: 'complete' | 'stopped' | string
}

export interface ConversationDetail extends ConversationOut {
  messages: MessageOut[]
}

export interface ChatRequest {
  message: string
  conversation_id: string | null
  attachments?: ChatAttachmentInput[]
}

export interface ChatResponse {
  attempt_id?: string | null
  assistant_message_id?: string | null
  conversation_id: string
  reply: string
  model: string | null
  sources?: SourceRef[]
  code_results?: CodeResult[]
  skill_names?: string[]
}

/**
 * 发送模式：`stream` 走 SSE 增量返回并附管线阶段；`once` 走普通 POST，
 * 等待完整结果一次性落地。**只有 `once` 会回传 `conversation_id`**，
 * 新建会话时无需靠列表排序去猜。
 */
export type SendMode = 'stream' | 'once'

/** 运行事件类型；所有 data 携带 run_id，传输帧有递增 id。 */
export type StreamEventType =
  | 'run.started'
  | 'node.started'
  | 'node.finished'
  | 'intent.classified'
  | 'plan.generated'
  | 'token'
  | 'tool.call'
  | 'tool.result'
  | 'citations'
  | 'code_result'
  | 'subtask'
  | 'run.finished'
  | 'heartbeat'
  | 'error'
  | 'rate_limit'
  | 'conversation'
  | 'message'

/** 图执行完成后的子任务摘要。只接受版本 1、名称为 research 或 draft、状态为 done。 */
export interface SubtaskSummary {
  name: 'research' | 'draft'
  status: 'done'
  summary: string
}

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
  is_current: boolean
  version_state: string
  deleted_at: string | null
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface DocumentChunkOut {
  id: string
  chunk_index: number
  content: string
  source: string | null
  strategy: string | null
  page: number | null
  section: string | null
  created_at: string
}


/** 引用原文核验的定位信息（页码 / 段落 / 源范围；缺失为 null，不推断）。 */
export interface ChunkLocatorOut {
  chunk_id: string
  chunk_index: number
  page: number | null
  section: string | null
  source_start: number | null
  source_end: number | null
  /** 仅父块在显式请求且通过独立鉴权后返回；默认为 null。 */
  content: string | null
}

/**
 * 引用原文核验结果（ADR-0007 只读核验）。
 *
 * 只有命中块本身的正文与定位信息，没有整章 / 整文档正文、源文件与控制面字段。
 */
export interface ChunkEvidenceOut {
  chunk_id: string
  document_id: string
  document_title: string
  version_state: string
  chunk_index: number
  content: string
  source: string | null
  page: number | null
  section: string | null
  source_start: number | null
  source_end: number | null
  parent: ChunkLocatorOut | null
}

export interface ImportJobOut {
  id: string
  status: string
  document_id: string | null
  error: string | null
}

export interface SearchResultOut {
  document_id: string
  content: string
  source: string | null
  score: number
  /** 可选以兼容旧版检索响应；缺失时页面标为未知，不推断父子关系。 */
  chunk_id?: string
  parent_id?: string | null
  chunk_kind?: 'parent' | 'child' | 'unknown'
  retrieval_origin?: 'hit' | 'parent_expansion'
  expanded_from_chunk_id?: string | null
  score_inherited_from_chunk_id?: string | null
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

export interface WorkflowAlertOut {
  id: string
  tenant_id: string
  workflow_id: string
  workflow_name: string
  owner_id: string
  owner_username: string | null
  reason: string
  detail: string | null
  created_at: string
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

export interface SkillListOut {
  id: string
  tenant_id: string
  owner_id: string
  owner_username: string | null
  tenant_name: string | null
  name: string
  description: string
  keywords: string[]
  source: 'builtin' | 'private' | 'global' | string
  scope: 'builtin' | 'private' | 'global' | string
  enabled: boolean
}

export interface SkillDetailOut extends SkillListOut {
  constraints: string
  system_prompt: string
  example: string
  version: string
}

export interface SkillWrite {
  name: string
  description: string
  keywords: string[]
  constraints: string
  system_prompt: string
  example: string
  scope?: 'private' | 'global'
}

export interface HealthInfo {
  status: string
  app: string
  version: string
  env: string
  checks?: {
    llm?: { mode: string }
  }
}
export type ReportFamily = 'retrieval' | 'generation' | 'agent' | 'workflow'
export type EvaluationTargetType = 'rag' | 'agent' | 'workflow'

export interface EvaluationTarget {
  id: string
  name: string
  target_type: EvaluationTargetType
  status: 'available' | 'owner_unavailable'
  metadata: Record<string, string | number | boolean | null>
}

export interface EvaluationTargetPage {
  items: EvaluationTarget[]
}

export interface EvaluationExecutionUser {
  id: string
  username: string
  role: 'tenant_admin' | 'member'
}

export interface EvaluationExecutionUserPage {
  items: EvaluationExecutionUser[]
}
export type ReportStatus = 'passed' | 'failed' | 'unmeasured' | 'unsupported' | 'unavailable'
export interface ReportMetric {
  name: string
  value: number | null
  unit: 'ratio' | 'count' | 'ms' | 'CNY'
}
export interface EvaluationReport {
  id: string
  family: ReportFamily
  scope: 'global' | 'tenant'
  status: ReportStatus
  protocol: string | null
  recorded_at: string | null
  metadata: Record<string, string | number | null>
  metrics: ReportMetric[]
}
export interface EvaluationReportDetail extends EvaluationReport {
  failures: { case_id: string; category: string | null; reason: string }[]
  failure_count: number
}
export interface EvaluationReportPage {
  items: EvaluationReport[]
  total: number
  page: number
  page_size: number
}
export interface EvaluationTrend {
  family: ReportFamily
  metric: string
  points: {
    report_id: string
    recorded_at: string | null
    value: number | null
    status: ReportStatus
    protocol: string | null
    metadata: Record<string, string | number | null>
  }[]
}

export type DatasetVersionStatus = 'draft' | 'pending_review' | 'approved' | 'rejected'
export type CandidateStatus = 'pending' | 'accepted' | 'rejected' | 'withdrawn'

export interface EvaluationDataset {
  id: string
  tenant_id: string
  name: string
  slug: string
  family: ReportFamily
  created_by: string
  description: string | null
}

export interface EvaluationDatasetPage {
  items: EvaluationDataset[]
  total: number
  page: number
  page_size: number
}

export interface EvaluationDatasetVersion {
  id: string
  dataset_id: string
  tenant_id: string
  version: string
  status: DatasetVersionStatus
  source: 'upload' | 'feedback' | 'ai_generated'
  content_hash: string
  schema_name: string
  case_count: number
  is_frozen: boolean
  created_by: string
  reviewed_by: string | null
  review_decision: string | null
  review_notes: string | null
}

export interface EvaluationDatasetVersionPage {
  items: EvaluationDatasetVersion[]
  total: number
  page: number
  page_size: number
}

export interface EvaluationCandidate {
  id: string
  tenant_id: string
  source_type: 'feedback' | 'rating'
  source_id: string
  attempt_id: string | null
  conversation_id: string | null
  status: CandidateStatus
  nominated_by: string
  source_user_id: string
  reviewed_by: string | null
  review_notes: string | null
  target_dataset_id: string | null
  snapshot: Record<string, unknown>
}

export interface EvaluationCandidatePage {
  items: EvaluationCandidate[]
  total: number
  page: number
  page_size: number
}

export type EvaluationJobStatus = 'queued' | 'running' | 'cancel_requested' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'

export interface EvaluationJob {
  id: string
  tenant_id: string
  submitted_by: string
  dataset_id: string
  dataset_version_id: string
  job_kind: 'internal_smoke' | 'real_resource'
  target_type: EvaluationTargetType | null
  adapter_name: string | null
  adapter_version: string | null
  metric_schema_version: string | null
  execution_user_id: string | null
  runner: ReportFamily
  mode: 'smoke' | 'synthetic' | 'real'
  status: EvaluationJobStatus
  input_hash: string
  max_external_calls: number
  external_calls_used: number
  case_total: number
  case_completed: number
  report_id: string | null
  report_hash: string | null
  error_code: string | null
  cancel_requested_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface EvaluationJobPage {
  items: EvaluationJob[]
  total: number
  page: number
  page_size: number
}

export interface EvaluationCaseResult {
  id: string
  job_id: string
  tenant_id: string
  case_id: string
  status: 'pending' | 'running' | 'passed' | 'failed' | 'error' | 'skipped'
  metrics: Record<string, unknown>
  assertions: { name?: string; passed?: boolean }[]
  evidence: Record<string, unknown>
  error_code: string | null
  elapsed_ms: number | null
  external_calls_used: number
}

export interface EvaluationCaseResultPage {
  items: EvaluationCaseResult[]
  total: number
  page: number
  page_size: number
}

export type RetrievalBackend = 'native' | 'langchain' | 'llamaindex'

export interface RetrievalVariant {
  backend: RetrievalBackend
  top_k: number
}

export interface RetrievalComparisonCandidate {
  rank: number
  chunk_id: string
  document_id: string
  source: string | null
  score: number
  similarity: number
  version_status: string
  retrieval_origin: string
  content_preview: string
}

export interface RetrievalComparisonRun {
  variant: RetrievalVariant
  effective_backend: string
  elapsed_ms: number
  candidates: RetrievalComparisonCandidate[]
}

export interface RetrievalComparison {
  identity: Record<string, string | number | null>
  baseline: RetrievalComparisonRun
  candidate: RetrievalComparisonRun
}

export interface RetrievalStrategies {
  backends: RetrievalBackend[]
  top_k_min: number
  top_k_max: number
}

export interface RunHistoryItem {
  id: string
  conversation_id: string
  status: 'succeeded' | 'failed'
  error_code: string | null
  started_at: string
  finished_at: string
  duration_ms: number
  llm_call_count: number
  tool_call_count: number
  input_tokens: number | null
  output_tokens: number | null
  usage_source: string | null
}

export interface RunHistoryDetail extends RunHistoryItem {
  events: {
    type: string
    name: string
    offset_ms: number
    duration_ms: number | null
    status: string | null
    round: number | null
  }[]
}

export interface RunHistoryPage {
  items: RunHistoryItem[]
  total: number
  page: number
  page_size: number
}
