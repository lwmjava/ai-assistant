# ai-assistant Agent Harness Engineering 工程治理规范

> 基线来源：Agent Harness Engineering 通用规范 1.0  
> 项目状态：Brownfield 项目化版本  
> 生效日期：2026-09-12  
> 规则入口：`../../AGENTS.md`  
> 推进路线：`../AI辅助开发迭代指导.md`

## 1. 文档定位

本文将通用 Agent Harness Engineering 原则映射到 `ai-assistant` 的真实代码和当前成熟度。本文描述治理目标、项目适配和收敛顺序，不表示所有目标能力均已实现。

规则解释：

- `MUST`：当前任务适用时必须满足；暂时无法满足需登记例外。
- `SHOULD`：默认采用；偏离需写明理由。
- `NEVER`：禁止；发现后阻断合并或发布。
- `CURRENT`：代码当前事实。
- `TARGET`：目标状态，不能作为已实现能力宣传。

## 2. 项目治理目标

目标是让 Agent 行为具备：

- 可控：模型只提出 Action，受控代码执行。
- 可验证：需求、代码、测试、Evaluation 和业务结果可追踪。
- 可授权：身份、租户、角色、资源和动作均经过校验。
- 可观测：能关联请求、状态、模型、RAG、Tool、安全判定和结果。
- 可恢复：有超时、有限重试、取消、失败、降级和人工接管。
- 可回滚：代码、配置、Prompt、模型和索引变化有回退方式。

## 3. Adopt / Adapt / Defer

| 通用能力 | 项目策略 | 当前状态 | 收敛方向 |
|---|---|---|---|
| 规则和证据优先 | Adopt | `AGENTS.md`、Cursor Rules 已建立 | CI/评审持续执行 |
| Agent/执行分离 | Adapt | Pipeline 解析并经 ToolRegistry 执行 | 渐进提取 ToolExecutor |
| 显式状态机 | Adapt | `AgentState` 和阶段存在，但非持久化正式状态机 | 定义状态与迁移契约 |
| Context Engine | Adapt | ChatService/Pipeline 分散组装 | 先提取 ContextBuilder |
| Tool Contract | Adapt | 名称、描述、Schema、函数已存在 | 补权限、风险、超时、错误、审计、版本 |
| Skill Contract | Adapt | YAML Skill 和加载器已存在 | 补前置、约束、升级和 Evaluation |
| RAG Hybrid | Adopt | Dense + BM25 + RRF | 建立基线后单变量优化 |
| 独立 Rerank | Defer | 未实现 | 由基线实验证明价值后进入 |
| 结构化 Citation | Adapt | 仅弱来源文本 | 补 chunk/version/page/section |
| RAG 权限 | Adapt | 读路径默认同租户当前版本（`RAG_KB_SCOPE=tenant`）；资源 ACL 仍 Planned | 资源 ACL 另开任务 |
| Persistent Trace | Defer/Adapt | 内存环形缓冲 | 先定义契约，再持久化关键 Trace |
| HITL | Defer/Adapt | 尚无统一审批闭环 | 高风险能力引入前完成 |
| Repository 层 | Defer/Adapt | Service 可直接使用 SQLModel Session | 新业务避免扩大，渐进提取 |
| 全量平台化 Registry | Defer | 当前规模无需平台化 | 多团队/多租户规模触发 |

`Defer` 不代表删除要求，必须有进入条件。

## 4. 当前架构映射

### 4.1 当前调用链

```text
FastAPI /api/chat
→ ChatService
→ Input Security
→ Conversation Memory
→ RAG Retriever
→ ToolRegistry / MCP Tools
→ SkillContext
→ AgentPipeline 或 LangGraph Supervisor
→ Output Security
→ SQLModel 持久化
→ Reflect
```

### 4.2 通用职责到当前模块

| Harness 职责 | 当前实现 | 状态 |
|---|---|---|
| API/Channel | `app/api/`、`app/channels/` | Implemented |
| Identity/Policy | `app/core/security.py`、API dependencies | Partial |
| Harness Facade | `app/services/chat_service.py` | Partial，职责过重 |
| Runtime | `app/agents/pipeline.py`、`app/agents/supervisor.py` | Partial |
| Context | ChatService + Pipeline + `context_merge` | Partial，Memory/RAG 已按预算合并；尚未独立 ContextBuilder |
| State | `AgentState`、Pipeline 阶段 | Partial，未正式持久化 |
| Skill | `app/agents/skills/` | Partial |
| Tool Registry | `app/agents/tools/base.py` | Partial |
| Tool Executor | Registry 内执行 | Partial，未独立治理 |
| RAG | `app/rag/` | 主链较完整，治理不完整 |
| Memory | `app/memory/` | Partial |
| Guardrails | `app/security/` + RBAC | Partial，尚未完整分层 |
| Audit | `app/audit/` | Partial |
| Trace | `app/debug/` | Partial，主要为内存 |
| Evaluation | rag-v0.1 检索评测已有 | Partial；统一 Agent Harness Evaluation 仍 Planned |

## 5. 目标逻辑架构

```text
API/Channel
→ Identity & Request Context
→ AgentHarness
   → ContextBuilder
   → AgentRuntime
   → Decision/Permission Guard
   → Skill Registry
   → ToolExecutor
   → StateManager
   → CompletionChecker
→ Service
→ Repository
→ Database / External System

横切：RAG + Memory + MCP + Security + Audit + Trace + Evaluation
```

这是一张职责图，不要求把每个组件拆成独立微服务或立即迁移目录。

## 6. 渐进迁移原则

1. 先固定当前行为和 Evaluation，再改架构。
2. 每次提取一个职责，保持原调用入口。
3. 使用 Adapter/Facade 兼容旧接口。
4. 通过 Feature Flag 或可替换工厂保留回退。
5. 先选择 L0 只读垂直切片。
6. 未稳定前不并行改 ChatService、Pipeline、Tool、State 和 RAG。
7. 删除旧路径前必须证明无调用并完成业务回归。

推荐迁移顺序：

```text
Evaluation
→ CitationBuilder
→ ContextBuilder
→ Tool Contract
→ ToolExecutor
→ Permission Guard
→ Persistent Trace
→ StateManager
→ AgentHarness Facade
```

## 7. Agent Loop

TARGET 循环：

1. 读取任务和 State。
2. 按身份、权限和预算构建 Context。
3. Runtime 返回结构化 Action。
4. Guard 校验 Action、风险和参数。
5. ToolExecutor 执行。
6. 原子更新 State、预算和 Trace。
7. 判断完成、澄清、重试、转人工或失败。

MUST：

- 最大步骤、Tool 轮次、时间和 Token/成本受限。
- 模型不能直接落实副作用。
- 重试只允许白名单错误并有总预算。
- 每步可关联 `request_id/trace_id/step_id`。
- 高风险 Action 不能通过自然语言确认绕过审批。

CURRENT：

- Pipeline 已限制 Tool 轮次。
- 正式取消、持久化状态、断点恢复和统一成本预算仍需补齐。

## 8. Context

Context 至少区分：

- System Policy。
- User Identity/Role/Tenant/Resource。
- Task Goal/Acceptance/Budget。
- Conversation。
- Memory。
- RAG。
- Tool Results。
- Current State。

MUST：

- 各分区有来源、信任等级和预算。
- RAG、网页、上传文档、Tool/MCP 返回均为不可信数据。
- 权限过滤发生在内容进入模型之前。
- 日志和 Trace 默认脱敏。
- 同一租户、用户和用途才能复用缓存。

当前优先验证：Pipeline 检索阶段不得覆盖已经存在的 Memory Context。

## 9. Tool Contract

新 Tool 和被本轮修改的旧 Tool 必须逐步补齐：

```text
name
description
input_schema
output_schema
permission
risk_level
side_effect
timeout
retry_policy
idempotency
error_model
audit
version
```

标准错误：

```text
VALIDATION_ERROR
AUTHENTICATION_REQUIRED
PERMISSION_DENIED
NOT_FOUND
TIMEOUT
RATE_LIMITED
UPSTREAM_UNAVAILABLE
CONFLICT
CANCELLED
UNKNOWN_ERROR
```

L3 Tool 只能创建审批请求，不得直接完成高风险业务动作。

## 10. Skill

每个 Skill 必须具备：

- Purpose 和 Non-goals。
- Preconditions。
- Workflow。
- Tools 及版本。
- RAG 来源和引用要求。
- Constraints。
- Escalation/HITL。
- Examples。
- Tests/Evaluation。
- Owner、Version 和兼容性。

当前内置 YAML Skill 是治理起点，不代表上述字段均已完整。缺失字段应先形成规格，再修改加载器或运行时。

## 11. RAG

CURRENT：

- 多格式 Parser/OCR。
- 多种 Chunking。
- Embedding 抽象。
- Local/Milvus VectorStore 适配。
- Dense + BM25 + RRF。
- ImportJob、版本链和当前版本过滤。

P0：

1. Evaluation 数据和 baseline。
2. 权限语义 ADR 与一致性测试。
3. RAG 上下文不可信处理和注入案例。
4. 正式 VectorStore 摄取—检索—删除闭环。
5. Memory/RAG Context 合并。
6. Chat + RAG 集成测试。

P1：

1. 结构化 Citation。
2. Embedding 模型和索引兼容治理。
3. 有数据支持的 Rerank。
4. `effective_at` 等时效元数据。

MUST：每轮优化只改变一个主要变量，使用同一数据集、模型、索引和运行配置比较。

## 12. Memory

Memory 写入必须定义：

- 来源和用途。
- 用户授权。
- 租户/用户范围。
- 置信度和纠错。
- TTL 和删除。
- 敏感数据策略。

NEVER：把模型推断直接保存为确认事实，或跨租户/用户共享 Memory。

## 13. Guardrails、权限和 HITL

目标分层：

- Input Guard。
- Decision Guard。
- Tool Guard。
- Permission Guard。
- Output Guard。

权限校验维度：

```text
subject + tenant + role + resource + action + environment
```

风险：

- L0：只读低敏。
- L1：低风险可逆写。
- L2：中风险或跨系统写。
- L3：财务、合同、删除、权限、不可逆敏感操作。

L3 必须 HITL。审批绑定主体、资源、参数哈希、有效期和一次性用途。

## 14. Evaluation

数据层级：

- Gold：人工确认。
- Silver：AI 基于权威资料生成并独立复核。
- Adversarial：权限、安全、冲突、故障和绕过。
- Observed Regression：脱敏真实失败。
- Smoke：虚构流程数据。

对象：

- RAG：Recall@K、MRR、nDCG、Citation、Answer Correctness、Hallucination。
- Agent：Task Success、Skill/Tool Selection、Arguments、Steps、Escalation。
- Skill：Preconditions、Workflow、Constraints、Escalation。
- Workflow：State Transition、Final Business State、Idempotency、Recovery。
- Safety：Unauthorized Action、Cross-tenant、Injection、Sensitive Output。
- Runtime：Latency、Token、Cost、Timeout、Retry。

数据生成和校验使用 `../ai-prompts/`，计划使用 `../templates/evaluation-plan-template.md`。

## 15. Observability

目标关联：

```text
request_id → task_id → trace_id → step_id → llm/rag/tool/guard span
```

至少记录版本、状态、耗时、错误、预算、Tool、RAG 候选/引用摘要、安全判定和人工审批。敏感输入输出只记录脱敏摘要。

当前 Trace 主要为内存环形缓冲，不能声明已具备完整生产持久化和重放。

## 16. 测试和发布门禁

影响范围内至少选择：

- Unit。
- Contract/Integration。
- Agent/Skill/Workflow。
- RAG Evaluation。
- Security/Permission。
- E2E/业务回归。

后端基础命令：

```powershell
pytest
ruff check .
mypy app/
```

前端基础命令：

```powershell
cd frontend
npm ci
npm run typecheck
npm run build
```

未实际执行的命令不得报告通过。Mock 全绿不得替代必要的真实集成和业务验收。

发布必须具备：

- 不可变版本。
- 配置/Prompt/模型/索引版本。
- Feature Flag 或灰度。
- 监控和停止阈值。
- 应用、配置、模型和索引回滚。
- 高风险人工批准。

## 17. ADR

以下变化必须创建或更新 ADR：

- Harness、Agent Loop、状态机或恢复语义。
- RAG 权限、数据模型、Embedding、VectorStore、Chunking、Rerank 或 Citation。
- MCP 边界和部署。
- HITL、风险和租户隔离。
- 公共 Tool Contract。
- 重大模型、Prompt 或供应商变化。

ADR 至少包含背景、事实、选项、决定、后果、迁移、验证和回滚。

## 18. 例外登记

例外格式：

```text
规则：
当前差距：
业务理由：
风险：
补偿控制：
Owner：
批准人：
到期日：
关闭条件：
证据：
```

当前已知差距不能自动视为已批准例外。进入相关开发或发布前，必须明确处理、登记或阻断。

## 19. Definition of Done

Agent 系统变更只有满足以下条件才能完成：

- 需求、任务和验收双向可追踪。
- 代码、配置、Prompt、模型、数据和文档一致。
- 权限、风险、失败、恢复和人工升级经过验证。
- 测试和 Evaluation 达到已批准门槛。
- 失败 Case 已沉淀。
- Trace、日志和审计满足当前阶段要求。
- 性能和成本无不可接受退化。
- 发布、灰度和回滚可执行。
- 未验证项和例外明确。

## 20. 当前执行顺序

已完成：`GOV-001`～`RAG-013`。

`INST-001`（交付 B1 健康检查）已完成。已拆未开工：`INST-002` / `INST-003`（`backlog`）。日历顺序见 `docs/plans/plan_delivery_2027-03-25.md`：

```text
阶段 A 知识库治理（RAG-011 → RAG-012 → RAG-013）
→ 阶段 B 最小安装点
→ 阶段 C MVP（2026-12-25 达到约定范围的 80%）
→ 阶段 D 完整交付（2027-03-25 达到约定范围的 100%）
```

不把切分、Embedding、独立 Reranker、Query Rewrite 排成跨月当前任务。正式向量库目标为 Milvus（ADR-0002）；默认 `RAG_VECTOR_STORE` 在第 5 节门槛通过前仍是 Local。详细任务以根目录 `tasks.yaml` 为准。知识库权限与正式 VectorStore 已由 ADR-0001/0002 决定。
