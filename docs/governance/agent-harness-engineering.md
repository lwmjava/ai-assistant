# ai-assistant Agent Harness Engineering 工程治理规范

> 基线来源：Agent Harness Engineering 通用规范 1.0  
> 项目状态：Brownfield 项目化版本  
> 生效日期：2026-09-12  
> 规则入口：`../../AGENTS.md`  
> 推进路线：`../AI辅助开发迭代指导.md`

> 2026-10-06 批准补记：RAG 引用核验遵 ADR-0007，Embedding 索引身份/切换遵 ADR-0008，模型预算、来源及摘要生命周期遵 ADR-0005 第9节；当前决定见 [推进清单](../plans/plan_rag_remaining_order_20261006.md)。这些是目标约束与实施授权，不把 TARGET 变成 CURRENT；真实费用/质量数值、重要数据重建和生产启用仍按具体证据授权。本文历史任务数量不替代最新 tasks.yaml。

> 阅读方式（2026-10-22）：仅在涉及运行时边界时读取相关章节。第3、4节与各CURRENT段为2026-09-12起的历史映射，不作为最新能力状态；相关任务实施时核对代码和当前证据。开发流程、记录与审查按AGENTS.md第9节，不为实现本文职责图另增开发阶段。默认VectorStore现为milvus，Milvus仍Partial。

## 使用边界与阅读索引

本文约束产品运行时 Agent 的执行行为，不是要求开发 Agent 每次完成一轮全套治理。
普通页面、局部缺陷和纯文档任务无需全文读取；目标职责图不产生新任务或重构授权。

| 当前修改 | 阅读章节 |
|---|---|
| Loop/Context/Memory | 7、8、12及适用ADR |
| Tool/Skill | 9、10、13 |
| RAG | 11、14及适用ADR |
| 权限/HITL | 13及适用ADR |
| 可观测性 | 15 |
| 边界决定/兼容迁移 | 5、6、17 |
| 测试与关闭 | AGENTS.md第9、10、13节；本文16、19节只补运行时适用项 |

目标与历史差距只在本任务确实触及、扩大或依赖时处理；无关既有缺口不要求本卡一次补齐。安全红线和已触发门禁仍有效。
已有规格覆盖的字段复用；修改局部字段不要求重写整份Tool/Skill规格或自动新增ADR。

## 1. 文档定位

本文将通用 Agent Harness Engineering 原则映射到 `ai-assistant` 的真实代码和当前成熟度。本文描述治理目标、项目适配和收敛顺序，不表示所有目标能力均已实现。开发治理与产品运行时Harness分别评价，流程减负不改变权限、预算、状态或安全契约。

规则解释：

- `MUST`：当前任务适用时必须满足；暂时无法满足需登记例外。
- `SHOULD`：默认采用；偏离需写明理由。
- `NEVER`：禁止；发现后阻断合并或发布。
- `CURRENT`：标注基线时点的代码事实；作为当前结论使用前须核对实现和有效证据。
- `TARGET`：目标状态，不能作为已实现能力宣传。

## 2. 项目治理目标

目标是让 Agent 行为具备：

- 可控：模型只提出 Action，受控代码执行。
- 可验证：需求、代码、测试、Evaluation 和业务结果可追踪。
- 可授权：身份、租户、角色、资源和动作均经过校验。
- 可观测：能关联请求、状态、模型、RAG、Tool、安全判定和结果。
- 可恢复：有超时、有限重试、取消、失败、降级和人工接管。
- 可回滚：代码、配置、Prompt、模型和索引变化有回退方式。

## 3. 历史 Adopt / Adapt / Defer 映射

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

## 4. 历史架构映射

### 4.1 基线调用链

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

### 4.2 通用职责到基线模块

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

命令、执行地点、证据复用及关闭条件统一由 AGENTS.md 第10节定义，本文不维护第二套命令。
按实际运行时变化选择Unit/Contract、Agent/RAG、权限、安全、恢复与必要真实业务证据，不要求每卡覆盖全部维度。
Mock不替代必要真实集成；未执行不能写通过。发布另需不可变版本、兼容回滚、监控停止阈值及授权；开发阶段不自动触发部署。

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

遵守 AGENTS.md 第13节。运行时变更额外核对本次影响的身份、预算、状态、失败恢复、Trace和真实质量证据。
仅修改文案不机械要求索引重建或全套Evaluation；实际改变模型决策或执行行为时保留对应案例及门禁。

## 20. 推进与文档维护

当前任务按 `tasks.yaml` 的依赖与进入条件推进；有效决定索引见 `AGENTS.md` 第14节。不在本文复制完成流水账或任务数量。历史映射供理解背景，不能作为重新实施已完成能力或忽略新证据的理由。

开发治理的阅读、风险分级、记录合并和评审退出统一由AGENTS.md第2、9节定义；测试/最终版本/发布门禁按第10节。新发现的确定性失败优先沉淀到正式tests/evals；工具和原始产物按第11节归属。
