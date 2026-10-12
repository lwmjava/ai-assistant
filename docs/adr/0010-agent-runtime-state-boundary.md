# ADR-0010 Agent 运行时分层：独立 Tool Executor 与 State Manager 边界及状态持久化

日期：2026-10-12。状态：Accepted（PAGT-006 任务范围内的架构边界决定；具体实现与验收以单卡证据为准，不以本 ADR 推导通过）。

授权依据：用户 2026-10-11 在 PAGT-006 卡内拍板落地边界——全量迁移既有 4 个内置工具与中间状态、实施前必须先按 AGENTS.md §4 落 ADR（变更 Harness/状态边界）、State 持久化方案（含新表）在本 ADR 中确定；2026-10-12 用户明确授权实施 PAGT-006。本 ADR 不授予生产操作、真实数据迁移、模型费用或 Git 发布权限。

## 背景与事实

Agent 主链当前能跑，是因为工具调用内嵌在 `AgentPipeline` 的行动循环里，编排中间状态全部散在内存对象上（`app/agents/pipeline.py:222` 的 `AgentState` dataclass）与对话记录表（`messages`）里，没有独立的执行与状态边界。评审（`docs/reviews/2026-10-04-9域企业上线完整度评审.md` §2.2）点出缺独立 Tool Executor / State Manager / Repository 层；AGENTS.md §4 的 Brownfield 约束又禁止一次性重写调用链。PAGT-006 卡非目标：不做 Repository 独立层、不一次性重写 `AgentPipeline` 调用链、不改既有 4 个内置工具对外行为。

经本次亲自读代码核对的事实（不以猜测代替）：

- `AgentState`（`pipeline.py:222-247`）为纯内存 dataclass：`user_input/history/understanding/plan/context/retrieval_*/tool_results/code_results/draft/reflection/answer/quality_score/revision/executed_tool_fingerprints/tool_budget_exhausted/delegations`。无任何持久化；进程结束即丢失，长工具链中断后无法续跑。
- 工具执行咽喉在 `ToolRegistry.run`（`app/agents/tools/base.py:199-243`）：已具备按名发现、RBAC 调用前拦截（`_denied_reason`）、`asyncio.timeout` 超时包裹、审计写入（`_record_audit`）与敏感参数脱敏（web_search/file_ops 打码、code_sandbox 路径净化）。**缺口**：不做 JSON Schema 参数校验；`ToolContract.max_retries`（`contract.py:58`，默认 0）在 `run()` 中从未被读取，重试能力名义存在、实际未接；返回值直接拼装成 `[name] 参数=… 结果=…` 字符串，无结构化结果契约。
- 管线调用点：`AgentPipeline._execute_tool`（`pipeline.py:436-442`）做检索指令围栏后直接 `self.tools.run(call)`；`_finish_tool`（444-452）把观测追加进 `state.tool_results`；行动循环 `_iter_action_loop`（783-831）每轮更新指纹集与预算耗尽标志，并向流式侧 yield `AgentEvent("tool"/"code_result", …)`。
- 流式事件契约：`AgentEvent`（250-258）仅 `type+data`，实际类型为 `stage/token/tool/code_result/done/error`；前端与 ChatService（`app/services/chat_service.py:392` 起）按此消费。
- 数据层现状：`app/models/` 仅有 `conversation/membership/skill/user/workflow/rag` 七组表，**无任何 Agent 运行时状态表**；`init_db`（`app/core/database.py:56`）先走 `SQLModel.metadata.create_all`（幂等建表），再走 alembic `auto_migrate`（`app/core/migration.py`；开发环境迁移失败不阻塞启动）。
- 4 个内置工具（`app/agents/tools/builtin.py`）：calculator(L0)、code_sandbox(L1)、get_current_datetime(L0)、web_fetch(L1)，契约 `max_retries` 均为默认 0；另有可选挂载的 web_search/file_ops/file_ops_write。

## 选项与决定

### 决定 1：Tool Executor 独立层——Registry 留注册职责，执行咽喉外移且向后兼容

**选项 A（选定）**：新增 `app/agents/tools/executor.py` 的 `ToolExecutor`，承担参数校验、超时、有限重试、权限/审计挂钩与结果归一化；`ToolRegistry` 保留注册/发现/`describe`/`describe_contracts` 职责，`run()` 降级为对 Executor 的薄委托；管线侧通过构造函数可选注入（`AgentPipeline(executor=…)`），未注入时走原路径。

**选项 B（否决）**：管线直接持有 Executor、删除 `Registry.run()`。会同时改动 Supervisor、测试与所有调用点，触碰"不一次性重写调用链"非目标。

**选项 C（否决）**：只在现有 `Registry.run` 里补校验与重试。执行逻辑继续与注册表耦合，工具规模扩展后拆债对象依旧存在，正中本卡要避免的情形。

决定要点：

- `ToolExecutor` 为无状态执行器，输入 `ToolCall` + 执行上下文（user_id/tenant_id 从 `log_context` 读取，沿用现状），输出结构化 `ExecutorResult` dataclass：`observation: str`（给模型的观测文本）、`status: ok/denied/invalid/timeout/error`、`attempts: int`、`latency_ms: float`、脱敏后的参数快照。管线只消费 `result.observation`，元数据进审计/Trace。
- **参数校验**：按 `Tool.parameters`（JSON Schema）做必填项与类型校验。为不改变既有 4 工具对外行为，v1 内置工具的校验失败**不提前拦截**——4 个工具自身已对缺参/坏参返回友好文案（如 calculator「未提供 expression 参数」），Executor 对这类情形放行并把校验结论记入审计 metadata；硬校验（拒绝执行）作为契约级开关，默认不开启。未来新工具可声明启用。
- **超时/重试**：沿用契约 `timeout_seconds`；重试仅当 `contract.idempotent=True` 且错误为瞬时类（超时/网络）时发生，次数上限 `contract.max_retries`（默认 0 = 不重试，与现状逐字节一致），退避固定短间隔。非幂等、L1 以上写工具不自动重试。
- **权限与审计**：复用 Registry 既有 `_denied_reason`/`_record_audit`（组合委托，不复制 RBAC 逻辑）；审计 details 继续只落 risk/allowed/reason/latency，不落 arguments、不落密钥。
- **观测文本格式不变**：`[name] 参数=已脱敏 结果=observation` 的拼装格式原样保留，保证模型 Prompt 与既有回归测试不漂移。

### 决定 2：State Manager——包装 AgentState，自身不碰数据库

**选项 A（选定）**：新增 `app/agents/state.py` 的 `StateManager`，持有并包装一次执行的 `AgentState`，暴露类型化的读写访问与快照组装（序列化子集）；持久化通过注入的协议接口调用 `app/services/agent_state_service.py`（服务层持有 Session）。管线构造函数可选接收 `StateManager`，未接收时维持现状直接就地改 `AgentState`。

**选项 B（否决）**：StateManager 直接 import Session/engine 写库。违反 AGENTS.md §5「agents 不得直接操作数据库」。

**选项 C（否决）**：不引入 StateManager，只把 checkpoint 逻辑塞进 ChatService。编排状态边界仍散落在服务层，状态读写与管线循环耦合，下一轮工具扩展时再次成为拆债对象。

决定要点：

- `StateManager` 不 import `app.core.database`、不 import `sqlmodel`；持久化依赖以 Protocol 注入（`AgentStateStore`：`save_checkpoint(state)` / `load_active(conversation_id, message_id)` / `discard(...)`），由 `app/services/agent_state_service.py` 实现（该层可放 `app/services/`，在卡 allowed_paths 内）。
- 与 `AgentState` 关系：StateManager 拥有/包装它；管线中计划、已用工具指纹集、预算轮次、续跑状态的读写统一经 StateManager；`executed_tool_fingerprints`（set）序列化时排序为列表。
- 流式兼容：checkpoint 仅在阶段边界写，不逐 token、不逐 LLM 阶段写；`AgentEvent` 契约不新增类型。

### 决定 3：状态持久化——新增 `agent_run_states` 表（checkpoint 行），而非复用 messages

**选项 A（选定）**：每「一次用户消息的执行」一行 checkpoint，JSON 快照列 + 绑定列 + TTL。

**选项 B（否决）**：在 `messages` 表加中间状态列。messages 语义是按轮次的最终问答内容；计划/工具结果/轮次/预算属于单次执行的中间态，塞进消息表会污染消息读路径、放大跨租户清理与导出（EXPORT-001 对话原文）范围，且无法表达「中断-续跑」的状态机。

**选项 C（否决）**：维持纯内存。不满足验收「长会话可续」。

表设计（SQLModel，风格对齐 `app/models/conversation.py` 与 `base.TimestampMixin`）：

```text
agent_run_states
- id              str  PK（uuid hex）
- tenant_id       str  index
- user_id         str  index
- conversation_id str  FK→conversations.id, index
- message_id      str | None  FK→messages.id, index   -- 本次执行对应的用户消息
- stage           str       -- understanding/plan/retrieval/action/reflect/respond/done/error
- round_no        int       -- 行动循环已用工具轮次
- tool_budget_exhausted bool
- status          str       -- running/suspended/done/error
- state_json      str (TEXT) -- AgentState 可序列化子集；不含密钥；工具结果已在执行层脱敏
- error           str | None
- expires_at      datetime | None
- created_at / updated_at（TimestampMixin）
唯一约束：(conversation_id, message_id)；清理索引：(tenant_id, expires_at)
```

读写/恢复语义：

- **写入时机**：每个工具轮结束（`_finish_tool` 之后）与终态（done/error）各写一次 checkpoint；LLM 阶段间不写。单次请求写次数 ≤ `max_tool_rounds`(10) + 1，不引入每轮 DB 写放大。默认 upsert 同一行（按唯一约束）。
- **恢复单元**：恢复面向「同一次用户消息」的中断续跑（进程重启/连接断开后继续未完成的工具链），不跨轮；跨轮上下文由 Memory 与 messages 承担，不在本卡范围。
- **恢复读取**：新 run 开始时按 `(conversation_id, message_id)` 查唯一活动行；无行、行过期或租户绑定不一致 → 全新 run。
- **失败恢复不破坏旧数据**：快照反序列化失败、行残缺或并发冲突时，丢弃该行并按全新 run 执行；绝不覆盖其他活动行，错误不回灌用户可见文本。
- **TTL/清理**：`expires_at = updated_at + AGENT_STATE_TTL_SECONDS`；惰性读跳过过期行 + 启动/定时删除过期行；表为纯追加-覆盖性质，不存业务原文。
- **安全**：快照只含 AgentState 可序列化子集；工具观测在 Executor 层已脱敏（web_search/file_ops 参数打码、code_sandbox 路径净化）；`AgentState` 本身不含任何密钥/连接串；快照禁止落 `LLM_API_KEY` 等配置（实现时以测试守门）。

配置项（加入 `app/core/config.py` 的 `Settings`，风格对齐现有 `AGENT_*` 字段）：

```text
AGENT_STATE_PERSIST_ENABLED: bool = True
AGENT_STATE_TTL_SECONDS:     int  = 604800   -- 7 天
AGENT_STATE_CHECKPOINT_TOOL_ROUND: bool = True
```

## 范围扩展点（待用户决策）

本卡 `allowed_paths` 为 `app/agents/`、`app/services/`、`app/core/`、`tests/`、`docs/`、`tasks.yaml`。但上述决定落到代码时必然触及：

1. **`app/models/agent_state.py`（新表模型）及 `app/models/__init__.py` 注册、`app/core/database.py:init_db` 的模型 import**——`app/models/` 不在 allowed_paths 内（init_db 在 `app/core/` 内，可改；模型文件本体不可改）。
2. **如需要 alembic 版本化迁移脚本**：`alembic/versions/<rev>_agent_run_states.py`——`alembic/` 不在 allowed_paths 内。说明：开发/测试库经 `create_all` 即可幂等建表；版本化脚本是生产库路径的配套，是否必须待实施时按迁移机制核对（对齐 `migration.py` 的 create_all→auto_migrate 顺序）。

替代评估过「复用现有表/列」（见决定 3 选项 B），结论是语义不符、影响面更大，因此仍选新表。

**待用户决策事项**：是否批准将 PAGT-006 的 `allowed_paths` 扩展至 `app/models/`（及视需要 `alembic/versions/`）。未获批准前，本 ADR 只落文本，不实施建表代码；实施计划落盘时将此扩展作为前置条件写明。

## 后果与兼容

- 4 个内置工具对外行为不变：函数体、缺参友好文案、观测拼装格式、`max_retries=0` 下无重试——逐项以 golden 字符串回归守门。
- 默认向量库仍 `local`，不触及 RAG；`RAG_*` 配置不变。
- 流式事件契约不变：不新增 `AgentEvent` 类型，SSE 前端消费不改。
- 单实例边界：状态表靠进程内 DB 事务与唯一约束保护，不引入分布式锁/跨实例租约；与 ADR-0009 相同的单进程边界声明，不证明多进程并发下同一 run 的抢占正确性。
- 性能：checkpoint 写入限定在工具轮/终态边界；无逐 token、逐 LLM 阶段 DB 写；SQLite busy timeout 沿用既有 30s。
- 安全：状态快照含对话上下文与工具结果，已在执行层脱敏；快照不进审计 details、不进 Trace prompt；TTL 到期清理。
- Brownfield 兼容：Executor/StateManager 均为管线构造函数可选项，未注入时走现有 `Registry.run()` 与就地状态路径，主链调用点不重写。

## 迁移

- 新表经 `SQLModel.metadata.create_all` 在启动时幂等创建（空表，无历史数据回填）；按迁移机制核对后按需补一份 alembic revision（位置见「范围扩展点」）。
- 无真实数据迁移、无既有表结构变更、无索引重建；开关默认开但上线后可经 `AGENT_STATE_PERSIST_ENABLED=false` 即时关闭。
- 旧版本代码读到新表字段时无影响（旧代码不引用该表）。

## 验证

- 证据级别要求 E2（隔离 SQLite 测试库 `data/test_ai_assistant.db` + 真实代码），不单靠 Mock。
- Executor：JSON Schema 校验结论入审计；超时包裹生效；幂等工具注入瞬时失败时按 `max_retries` 重试、非幂等不重试；RBAC 拒绝路径与审计写入与现状一致；脱敏参数不出现在快照。
- 状态：工具轮 checkpoint 落库、终态落库；提交后用**新会话/新 Session** 从隔离库读回断言（不以同一对象保有值证明持久化）；损坏快照降级全新 run 且旧行不被改写；过期快照忽略；跨租户不可见；快照不含密钥的守门测试。
- 主链回归：`pytest -k "agent or tool or chat"`、4 个内置工具观测文本 golden 回归、`ruff check .` 与 `mypy app/`（用 `D:\DepTooL\anaconda3\envs\ai-assistant\Scripts` 解释器）。
- 失败边界反例（对齐 task-card-delivery 失败边界清单）：执行层超时、重试耗尽、校验非法参数、越权调用、快照损坏/过期、并发写冲突、租户绑定不一致。

## 回滚

- 配置回滚：`AGENT_STATE_PERSIST_ENABLED=false` 即停 checkpoint，主链走原内存路径。
- 代码回滚：回退分层提交，管线恢复 `Registry.run()` 内嵌调用；新表保留为空表或历史行，不做 schema 降级、不删数据、不删索引。
- 回滚到旧代码后，中断续跑能力消失，须人工核查进行中的 run；不把代码回退当作数据恢复方式。
