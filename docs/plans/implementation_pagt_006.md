# PAGT-006 实现说明：独立 Tool Executor + State Manager 层

- 卡：PAGT-006（依赖 PAGT-002，前置卡 HEAD 复核通过，见 `docs/reviews/2026-10-12-PAGT-002-head-recheck.md`）
- ADR：`docs/adr/0010-agent-runtime-state-boundary.md`（Accepted，决定 1 Executor / 决定 2 StateManager / 决定 3 agent_run_states 表与配置）
- 计划：`docs/plans/plan_pagt_006_20261012.md`；修复轮：`docs/plans/plan_pagt_006_review_fixes_20261012.md`
- 解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（conda，非 base）；隔离测试库 `data/test_ai_assistant.db`
- 状态：未 git 提交；本卡保持未完成，等待独立人工审核 Agent 结论与用户业务验收。

## 1. 功能实际完成了什么

### 1.1 谁可以做什么
- **租户内任意已认证用户**：正常对话（`/api/chat`、`/api/chat/stream`）时，工具调用统一经新的 `ToolExecutor`；编排中间状态由 `StateManager` 管理，并在工具轮/终态写 checkpoint。
- **未注入 Executor/StateManager 的路径**（Supervisor、fast path、测试直接构造 pipeline）：行为与基线 `0b08c2ee` 完全一致，走原 `Registry.run` 与就地状态。

### 1.2 Tool Executor（`app/agents/tools/executor.py`）
- 统一工具发现、参数校验（v1 内置工具不提前拦截、其自身返回友好文案；`strict_validation=True` 契约硬拒绝且 func 零调用）、超时（`asyncio.timeout(contract.timeout_seconds)`）、幂等瞬时重试（仅 `idempotent=True` 且错误属 `TimeoutError/ConnectionError`，次数上限 `max_retries`，默认 0 与现状逐字节一致）、RBAC 与审计**组合委托** `ToolRegistry` 的 `_denied_reason`/`_record_audit`（不复制逻辑）、结果归一化。
- 观测拼装收敛为共享函数 `app/agents/tools/base.py::format_tool_observation(name, arguments, observation)`，bound 与 legacy 同源，靠 golden 逐字节守门。
- 审计语义：一次逻辑调用（含全部重试）只写一条 `tool_call` 审计，`details` 恒 `risk/allowed/reason/latency_ms` 四键；参数校验结论只进 `ExecutorResult.audit_metadata`（供 Trace），**不进审计 details、不落 arguments/密钥**。

### 1.3 State Manager（`app/agents/state.py` + `app/services/agent_state_service.py`）
- `StateManager` 包装 `AgentState`，负责可序列化子集（understanding/plan/context/retrieval_*/tool_results/tool_budget_exhausted）的快照组装与恢复；`executed_tool_fingerprints` 由 set 序列化为排序 list；**本模块零 sqlmodel/DB import**。
- 持久化由 `AgentStateService`（服务层持 Session，实现 `AgentStateStore` Protocol）承担，新表 `agent_run_states`。
- **中断续跑语义（修复轮后）**：`load_active` 只恢复 `status in {running, suspended}` 的行；终态 `done/error` 行直接 discard、按全新 run。checkpoint 绑定**真实用户消息行 id**（非 NULL），每条用户消息独占一行 → 跨消息隔离，UNIQUE(conversation_id, message_id) 真实生效。

### 1.4 成功与失败时的行为
- **成功**：工具正常执行、观测写回 state、审计聚合一条；工具轮与终态写 checkpoint；流式事件契约不变。
- **失败降级（不阻断主链）**：checkpoint 写/读失败回滚并记日志、主链继续；超时返回「执行超时」观测；非幂等工具出错不自动重试；strict 校验失败不调用 func；RBAC 拒绝返回「未授权」且 func 零调用。
- **回滚**：回退分层提交即恢复 `Registry.run` 内嵌调用；新表保留为空表，无 schema 降级。

## 2. 代码位置（文件清单与职责）

| 文件 | 变更 | 职责 |
|---|---|---|
| `app/agents/tools/executor.py` | 新增 | ToolExecutor + ExecutorResult + `_validate` + 幂等瞬时重试 |
| `app/agents/tools/base.py` | 改 | 共享 `format_tool_observation`；`ToolRegistry` 加 `bind_executor`、`run` 绑定后薄委托 |
| `app/agents/tools/contract.py` | 改 | `ToolContract` 加 `strict_validation: bool = False` |
| `app/agents/state.py` | 新增 | StateManager + AgentStateStore Protocol（零 DB import） |
| `app/models/agent_state.py` | 新增 | `agent_run_states` 表，UniqueConstraint(conv, message_id) |
| `app/models/__init__.py` | 改 | 注册 AgentRunState |
| `app/core/database.py` | 改 | init_db 模型导入接线 |
| `app/core/config.py` | 改 | `AGENT_STATE_PERSIST_ENABLED/AGENT_STATE_TTL_SECONDS/AGENT_STATE_CHECKPOINT_TOOL_ROUND` |
| `app/services/agent_state_service.py` | 新增 | 持 Session 实现协议：upsert/load_active（状态过滤+过期+损坏+跨租户）/discard |
| `app/agents/pipeline.py` | 改 | 构造函数可选注入 executor/state_store/state_binding；`_execute_tool` 优先 executor；`_resume_state`/`_checkpoint`；run/run_stream 起始 resume、终态写 done |
| `app/services/chat_service.py` | 改 | `_wire_runtime` 注入 executor 与（开关开时）state_store，绑真实 message_id |
| `app/services/quota.py` | 改 | `accept_user_message` 返回 `(conversation, user_message_id)` |
| `tests/test_tool_executor.py` | 新增 | Executor 层 16 用例 |
| `tests/test_agent_state.py` | 新增 | StateManager + 持久化 13 用例（含终态不续跑） |
| `tests/test_agent_state_resume.py` | 新增 | 生产绑定多轮隔离/续跑/失败路径/事件契约/并发/4 工具 golden 8 用例 |
| `tests/test_rag_log_minimization.py` | 改 | `_accept_user_message` monkeypatch 适配元组返回 |

## 3. 验证命令与结果（修复轮后，实际退出码）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -m pytest tests/test_agent_state.py tests/test_agent_state_resume.py -q` | 0 | 21 passed |
| `python -m pytest -k "agent or tool or chat or quota or rag_log" -q` | 0 | 279 passed / 0 失败（1505 deselected） |
| `ruff check . --no-cache` | 0 | All checks passed |
| `mypy app/` | 0 | Success, no issues in 199 files |

持久化证据为 E2（真实代码 + 隔离 SQLite），且读回用新 Session/新句柄断言，非同一对象保有值。

## 4. 迁移机制核对结论（不落 alembic revision）

按 `app/core/migration.py` 的 `create_all → auto_migrate` 顺序核对：开发/测试库在 `init_db()` 由 `SQLModel.metadata.create_all()` 幂等建出新表 `agent_run_states`（测试已用隔离库建表跑通）。该表为全新空表、无历史 schema 需迁移，与既有 `rag_import_batches/jobs/traces` 表「create_all 建表、alembic stamp 越过」的 Brownfield 模式一致。故**不新增 `alembic/versions/` revision**；回滚时新表保留为空表、无 schema 降级。

## 5. 明确没做的事（未验证/未同步项）

- **根目录 `README.md`/`.env.example` 未同步** 三个 `AGENT_STATE_*` 配置项：二者在仓库根、不在 PAGT-006 allowed_paths 内，按计划 S10 登记为**已记录偏差**，不越界修改。
- **D-002 NULL 兜底路径未做多进程并发压测**：生产 message_id 已非 NULL、UNIQUE 真实生效；NULL 仅作单元/降级兜底，并发孤儿行为代码走查推断，未实跑多进程并发。
- **web_fetch golden cases 未入测试文件**：由独立验证冒烟脚本实测 bound/legacy 逐字节相等（见 verification §3 不变量①），未沉淀为 pytest 用例。
- **L2 写拒绝**：由既有 `tests/test_tool_audit.py::test_write_denied_for_member_role` 承载（PAGT-002），本卡未重复落新用例。
- **Repository 独立层不在本卡交付**：新代码走服务层薄封装 `app/services/agent_state_service.py`（与 ADR-0010、non_goal① 一致）。
- **PostgreSQL 生产验证（E1）未做**：本卡验证基于 SQLite 隔离库（E2），未在 PostgreSQL 实测。
- 本阶段未 git 提交/推送/合并；未写人工审核结论。

## 2026-10-13 评审修复补记

本页以上为原实现历史证据。后续评审发现 F01–F07，并在用户批准恢复入口及范围扩展后修复；README/.env.example 未同步的偏差已关闭。最新实际行为、验证、独立复审及残余限制以 [本轮实现说明](implementation_pagt_006_review_fixes_20261013.md) 为准，不沿用旧无 P1/P2 结论替代本轮检查。
