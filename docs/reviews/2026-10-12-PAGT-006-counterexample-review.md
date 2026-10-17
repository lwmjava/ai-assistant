# PAGT-006 反例审查结论（入口级独立审查）

- 审查日期：2026-10-12
- 审查人：入口级反例审查 Agent（**未参与实现**），会话身份 `s_000EtlRcF7j`，父 Agent `o_000EtuKI7gc`
- 审查对象：PAGT-006「独立 Tool Executor + State Manager 层」实现
- 等价审查说明：仓库技能目录仅 `module-batch-delivery` 与 `task-card-delivery`，**不存在 `code-review-quality` 技能**；本审查按 task-card-delivery 失败边界清单的等价方式执行——从真实领域入口自行构造反例并实际运行，未调用任何不存在的技能。

---

## 1. 审查范围与代码指纹

### 1.1 范围（文件清单）

| 文件 | 角色 |
|---|---|
| `tasks.yaml`（8885–8939 行） | PAGT-006 原始契约 |
| `docs/adr/0010-agent-runtime-state-boundary.md` | 架构决定 |
| `docs/plans/plan_pagt_006_20261012.md` | 实施计划 |
| `app/agents/pipeline.py` | AgentPipeline、_execute_tool/_finish_tool/_iter_action_loop/run_stream |
| `app/agents/tools/base.py` | ToolRegistry.run legacy 路径、format_tool_observation、bind_executor |
| `app/agents/tools/executor.py` | ToolExecutor / ExecutorResult（新增） |
| `app/agents/tools/contract.py` | ToolContract（增 strict_validation） |
| `app/agents/state.py` | StateManager / AgentStateStore Protocol（新增） |
| `app/models/agent_state.py` | AgentRunState 表（新增） |
| `app/services/agent_state_service.py` | AgentStateService 持久化实现（新增） |
| `app/services/chat_service.py` | chat/chat_stream 真实入口、_wire_runtime |
| `app/core/config.py` | AGENT_STATE_* 配置项 |
| `app/core/database.py` | init_db side-effect import |
| `app/models/__init__.py` | 模型注册 |
| `tests/test_agent_state.py` / `tests/test_tool_executor.py` | 新增测试 |

### 1.2 Git diff 统计（工作区 vs HEAD `0b08c2e`）

```
 M app/agents/pipeline.py       | 54 +++
 M app/agents/tools/base.py     | 49 +++-
 M app/agents/tools/contract.py |  4 +
 M app/core/config.py           |  6 +
 M app/core/database.py         |  1 +
 M app/models/__init__.py       | 23 +-
 M app/services/chat_service.py  | 25 +++
 M docs/adr/README.md           |  5 +-
?? app/agents/state.py
?? app/agents/tools/executor.py
?? app/models/agent_state.py
?? app/services/agent_state_service.py
?? docs/adr/0010-agent-runtime-state-boundary.md
?? docs/plans/plan_pagt_006_20261012.md
?? tests/test_agent_state.py
?? tests/test_tool_executor.py
```

基线 HEAD：`0b08c2e feat(pipeline): 为工具调用增加预算上限并在耗尽后停止行动`。

### 1.3 关键文件 SHA-256（工作区当前）

| 文件 | SHA-256 |
|---|---|
| app/agents/pipeline.py | `BE75C5E4BE8AAAD1A7AFEF855E3474D0D9F5515A69883492BA11A919FACF71ED` |
| app/agents/tools/base.py | `01A246EEA574FB5A560A75DD787A3208F796A04DE331B683E3DD14491D2E8F27` |
| app/agents/tools/executor.py | `236DF31FF3C4B9D8873A3028AC72896FC70E58EF899B3F252394844E52A93D65` |
| app/agents/tools/contract.py | `D9CFB7A737B0F5C93760531E68D72D004A83815D71A3ED06858DC9C96D8D63F4` |
| app/agents/state.py | `C5BE44E280C7AE1E48A4B5CE6F9CC99B18E1DC70AB467BA340F97EDCBECA2EB7` |
| app/models/agent_state.py | `9E0BD43FD3562C8600D7C2199E235478D0250AD2E53D76FA570644E57F841424` |
| app/services/agent_state_service.py | `0CE742996BB34EC13394DDAF1AE160D28D5BE36E74102D0F79025A68363D48F8` |
| app/services/chat_service.py | `5C09DB103DC548EE5C5136E4560EA6DD9DAC691C54EA8CB01E92E61BCD583A82` |
| app/core/config.py | `74895A3DFA7DDB831FB5CD52968164DA5ED87C50A4AF07CFF1117278353D5432` |
| app/core/database.py | `4F3139D3D53F543B1EA95ABE50A1C49AC39F1B9D6FE33D6B1B05ED3D86C2B57A` |
| app/models/__init__.py | `C5CA06B1AAF7661E9549778DE0C69E010F377B8D629551848F203A97B724D841` |
| tests/test_agent_state.py | `C98A3A7A766D37F5F3E31389B1879F7DD81661446AF99615F645B84447491944` |
| tests/test_tool_executor.py | `5431ADB519D0B4EA21E9E46B7178ABA4A784F49991BD9E004C9A2AF7CB87DDCE` |

---

## 2. 尝试过的反例及结果

### 反例 1（重点）：真实 ChatService.chat 链路下 message_id=None 的跨轮状态污染

- **入口**：`ChatService.chat(session, user, message, conversation_id)`——非流式真实入口，经 `_wire_runtime` 注入 Executor 与 StateManager。
- **注入**：
  - 脚本 LLM 第一次行动阶段返回 `<tool_call>{"name":"calculator","arguments":{"expression":"1+1"}}</tool_call>`；
  - 第二次、第三次同一会话返回"直接回答，不调用工具"。
  - log_context 设置 user_id/tenant_id（与真实认证中间件一致）。
- **期望**：按实施计划 §2.6 与 ADR-0010 决定 3「跨轮不续（跨轮由 Memory/messages 承担，非目标）」——第二次用户消息应是全新 run，state 不含第一次的 plan/tool_results。
- **实际**：
  - 第一次后表 1 行：`id=af8d9dc0, msg_id=None, stage=done, round_no=1`，state_json 含 calculator 观测。
  - 第二次后**同一行被覆盖**（id 不变），但 state_json 中：
    - `plan` 仍为 `"用户想算一道数学题。"`（第一次的规划）；
    - `tool_results` 仍含 `"[calculator] 参数={'expression': '1+1'} 结果=2"`（第一次的工具观测）；
    - `retrieval_disclosure` 被第一次的值覆盖。
  - 第三次后仍是同一行，继续覆盖。
- **证据**：脚本输出关键判定行——
  ```
  [污染] 行 af8d9dc0 的 state_json 仍含 'calculator'，
  说明第二次 chat 被第一次 done checkpoint 污染
  ```
- **根因**：
  1. `app/services/chat_service.py:301` `_wire_runtime` 硬编码 `"message_id": None`；
  2. `app/services/quota.py:147-150` `accept_user_message` 创建用户消息行但**不返回其 id**，`_wire_runtime` 拿不到 message_id；
  3. `app/services/agent_state_service.py:39-51` `_find_row` 在 Python 层"按 conv_id+tenant_id 查全部行，再找第一行 message_id is None 的行"——这导致**同一 conv 的所有用户消息共享同一行 NULL message_id 的 checkpoint**；
  4. `app/agents/pipeline.py:456-465` `_resume_state` 在每次 run 开始时调用 `load_active(conv_id, None)`，把上一次 done 行的快照 apply_snapshot 进新 state。
- **影响**：
  - A2「长会话可续」语义被反转——不是"中断后续跑同一次用户消息"，而是"不同用户消息之间状态泄漏"；
  - 模型在第二次行动阶段看到的 prompt 含第一次的"已调用工具结果"，会误导模型以为 calculator 已经算过 1+1=2；
  - 实施计划明确声明"跨轮不续"，实现却恰恰因为 message_id=None 而跨轮污染。
- **分级**：**P1**（直接违反 acceptance ② 的语义边界，且会污染模型 prompt，影响回答质量）。

### 反例 1b：UNIQUE(conversation_id, message_id) 在 SQLite 下对 NULL 不去重

- **入口**：直接 SQL 操作隔离测试库 `data/test_ai_assistant.db`。
- **注入**：INSERT 两行同 `conversation_id='conv-null-test'`、`message_id=NULL`。
- **期望**：UNIQUE 约束阻止第二行。
- **实际**：两行都插入成功，行数=2；PRAGMA 显示 `sqlite_autoindex_agent_run_states_2` 是 unique 索引但对 NULL 不生效（SQLite 标准行为）。
- **证据**：
  ```
  直接插入两行 NULL message_id 成功，行数=2
    -> UNIQUE 对 NULL 不去重！
  ```
- **当前缓解**：service 层 `_find_row` 在 Python 层做了"找第一行 NULL"的补偿，所以单线程下 save_checkpoint 不会堆积新行。
- **残余风险**：
  - 并发两个请求同时 `_find_row` 都返回 None，各自 INSERT，导致孤儿行堆积；
  - 孤儿行永远不会被 `_find_row` 选中（它只取第一行），TTL 到期前一直占空间；
  - 跨租户清理（OPS-005）按 (tenant_id, expires_at) 删时，孤儿行也能被清理，但运行期无并发保护。
- **分级**：**P2**（单线程下不触发，但并发下有堆积风险，且与 ADR-0010 决定 3「默认 upsert 同一行（按唯一约束）」的设计意图不符——唯一约束并未真正承担去重职责）。

### 反例 2：AGENT_STATE_PERSIST_ENABLED=false 时零行写入

- **入口**：直接构造 `AgentPipeline` + `AgentStateService`，调用 `_checkpoint`。
- **注入**：`settings.AGENT_STATE_PERSIST_ENABLED = False`。
- **期望**：表 0 行。
- **实际**：表 0 行。
- **证据**：`开关关时表行数 = 0 (期望 0)`。
- **分级**：通过。

### 反例 3：未绑定 Executor 时 legacy Registry.run 路径不变

- **入口**：`ToolRegistry(default_tools())`，未调用 `bind_executor`。
- **注入**：`calculator(expression=1+1)`。
- **期望**：观测文本与 HEAD 基线一致。
- **实际**：
  - legacy 观测：`[calculator] 参数={'expression': '1+1'} 结果=2`
  - bound 观测（bind 后）：逐字节一致
- **证据**：`[OK] bound/legacy 观测逐字节一致`。
- **分级**：通过。

### 反例 4：伪造 tenant_id/user_id 参数被拒

- **入口**：`Registry.run(ToolCall(name="calculator", arguments={"expression":"1","tenant_id":"fake"}))`。
- **期望**：返回"调用未授权"，func 零调用，审计 allowed=false。
- **实际**：返回 `[工具调用被拒绝] calculator：调用未授权。`。
- **证据**：`[OK] 伪造身份参数被拒`。
- **分级**：通过（与 PAGT-002 现状一致）。

### 反例 5：观测拼装格式与打码/净化规则

- **入口**：`format_tool_observation` 共享 helper。
- **期望**：calculator 不打码、web_search 参数 [redacted]、code_sandbox 宿主路径 <sandbox>。
- **实际**：
  - calculator：`[calculator] 参数={'expression': '1+1'} 结果=2`
  - web_search：`[web_search] 参数={'input': '[redacted]'} 结果=结果`
  - code_sandbox：`[code_sandbox] 参数={'code': 'x', 'host_path': '<sandbox>'} 结果=done`
- **分级**：通过。

### 反例 6：流式 AgentEvent 类型集合不新增

- **入口**：`AgentPipeline.run_stream(state)`，ScriptLLM stream_chat 返回"你/好/。"。
- **期望**：事件类型 ⊆ {stage, token, tool, code_result, done, error, conversation, rate_limit}。
- **实际**：收集到 `stage/token/done`，无新增类型。
- **证据**：`[OK] 流式事件类型集合 ⊆ 既有契约`。
- **补充（代码走查）**：checkpoint 只在 `_finish_tool` 后（pipeline.py:499-500）和终态（pipeline.py:989）写，不在 token yield 循环里写——无逐 token DB 写放大。
- **分级**：通过。

### 反例 7：核心回归测试不回归

- **入口**：`pytest tests/test_agent_state.py tests/test_tool_executor.py tests/test_pipeline_budget.py tests/test_chat.py tests/test_tool_audit.py`
- **实际**：**64 passed**，0 failed。
- **补充**：全量 `-k "agent or tool or chat"` 跑出 188 passed + 52 errors；抽查 error 详情为 Windows 下 pytest basetemp 清理的 `PermissionError: [WinError 5]`（conftest.py 注释已声明此为 Windows 已知问题），**非 PAGT-006 引入的回归**。
- **分级**：通过。

### 反例 8（未独立构造，代码走查确认）：StateManager 零 DB import

- `app/agents/state.py` 仅 import `json`、`typing`、`TYPE_CHECKING` 下的 `AgentState`；不 import `app.core.database`/`sqlmodel`/`app.models`。
- `tests/test_agent_state.py::test_state_manager_module_has_no_db_import` 已覆盖，通过。
- **分级**：通过。

### 反例 9（未独立构造，代码走查确认）：审计聚合四键

- `executor.py:172` 一次逻辑调用（含全部重试）只调一次 `_record_audit(..., allowed=True, reason=None, latency_ms)`；details 恒 `risk/allowed/reason/latency_ms` 四键，不落 arguments/密钥。
- strict 校验失败与 RBAC 拒绝路径各写一条 `allowed=False` 审计。
- **分级**：通过（与 ADR-0010 决定 1 一致）。

---

## 3. 缺陷清单

| 编号 | 级别 | 描述 | 位置 | 复现 |
|---|---|---|---|---|
| D-001 | **P1** | 跨轮状态污染：`_wire_runtime` 硬编码 `message_id=None`，同一 conv 的不同用户消息共享同一行 checkpoint；第二次 chat 开始时 `_resume_state` 错误恢复第一次 done 行的 plan/tool_results，污染模型 prompt | `app/services/chat_service.py:301`；`app/services/quota.py:147-150`（不返回用户消息行 id）；`app/services/agent_state_service.py:39-51`；`app/agents/pipeline.py:456-465` | `data/pagt006_counterexample.py` 实跑：第二次 state_json 仍含 `plan="用户想算一道数学题。"` 与 `tool_results="[calculator] ..."` |
| D-002 | **P2** | UNIQUE(conversation_id, message_id) 在 SQLite 下对 NULL 不去重；service 层 Python 补偿仅单线程安全，并发下两请求同时 `_find_row=None` 各自 INSERT 会产生孤儿行；ADR-0010 决定 3 声称"按唯一约束 upsert"但唯一约束实际未承担去重 | `app/models/agent_state.py:27-29`；`app/services/agent_state_service.py:69-84` | 直接 SQL 插两行 NULL message_id 成功（行数=2） |
| D-003 | **P2** | 验证证据文件缺失：实施计划 §6 要求的实现说明与验证证据（`docs/reviews/2026-10-12-PAGT-006-verification.md` 或 `docs/plans/implementation_pagt_006*.md`）未落盘；仅存在 plan-review 与 plan-recheck-2 | `docs/reviews/` | 文件系统检查 |
| D-004 | P3 | `.env.example`/README 未同步 `AGENT_STATE_*` 配置项（已在实施计划 S10 登记为已记录偏差，可接受） | 根目录 `.env.example`、README | — |

---

## 4. 总体结论

### 4.1 已通过的部分

- 独立 Tool Executor 分层落地：Executor 组合委托 Registry 的 RBAC/审计，未复制逻辑；观测拼装收敛为 `format_tool_observation` 单一 helper，bound/legacy 逐字节一致。
- StateManager 零 DB import，遵守 AGENTS.md §5。
- 开关回滚路径有效（`AGENT_STATE_PERSIST_ENABLED=false` 零行写入）。
- 伪造身份参数被拒、web_search/file_ops 参数打码、code_sandbox 路径净化。
- 流式 AgentEvent 契约不新增类型，checkpoint 不逐 token 写。
- 核心回归测试 64 passed。

### 4.2 阻断性问题

**存在未修复 P1 缺陷（D-001），按审查要求"存在未修复 P1/P2 时禁止关闭本卡"——PAGT-006 当前不得关闭。**

D-001 的本质是：实施计划 §2.6 与 ADR-0010 决定 3 明确声明"跨轮不续"，但实现因为 `message_id=None` 硬编码而**反向**造成了跨轮污染——第二次用户消息的 AgentState 错误继承了第一次的 plan 和 tool_results。这不是"中断续跑"（那是 A2 的正向目标），而是"跨轮状态泄漏"，会直接误导模型在第二轮的回答。

### 4.3 修复建议（不代替实现者决策）

1. **D-001 修复方向**：`accept_user_message` 返回新建用户消息行 id；`_wire_runtime` 把该 id 注入 `state_binding["message_id"]`；`_resume_state` 只在 `(conversation_id, message_id)` 绑定到同一次用户消息时才恢复。这样"同一次用户消息中断后续跑"（A2 正向）与"跨轮全新 run"（非目标）的语义边界才真正闭合。
2. **D-002 修复方向**：要么在 `save_checkpoint` 用 `INSERT ... ON CONFLICT DO UPDATE`（SQLite 3.24+ 支持，但需处理 NULL message_id 的冲突目标）；要么在 service 层对 message_id=None 的行加额外并发保护；要么把"同一次用户消息"的绑定列改为非 NULL（依赖 D-001 先修复）。
3. **D-003 修复方向**：补写实现说明与验证证据，落 `docs/plans/implementation_pagt_006_*.md` 或 `docs/reviews/2026-10-12-PAGT-006-verification.md`，写明实际命令、退出码、新 Session 读回证据。

### 4.4 审查边界

- 本审查未启动真实 LLM（用 ScriptLLM），未验证真实 Embedding/检索质量（非本卡范围）。
- 未做多进程并发压力测试（D-002 的并发风险为代码走查推断，未实跑并发场景）。
- 未运行 ruff/mypy（实施计划 §6 要求，但非本反例审查的必跑项；建议实现者在修复 D-001/D-002 后一并跑）。
