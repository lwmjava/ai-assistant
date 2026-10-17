# PAGT-006 Review 驱动修复计划（2026-10-12）

来源：`docs/reviews/2026-10-12-PAGT-006-verification.md`（独立验证）与
`docs/reviews/2026-10-12-PAGT-006-counterexample-review.md`（入口级反例审查）。
本文件先落盘缺陷→根因→修复→回归测试→受影响验证；修复完成后回填证据。

## 1. 缺陷清单与根因

| 编号 | 级别 | 缺陷 | 根因 |
|---|---|---|---|
| P1-1（D-001） | 阻断 | 跨消息 checkpoint 污染：生产 `_wire_runtime` 绑 `message_id=None`；`load_active` 不过滤 `status`；终态 done 行从不 discard。第二条消息把上一条 done 的 tool_results/context/understanding/fingerprints 搬进新 state，合法同名同参工具被残留指纹静默 skip。 | ① `quota.accept_user_message` 创建用户 Message 行但不返回其 id；② `_wire_runtime` 硬编码 `message_id=None`；③ `AgentStateService.load_active` 只跳过过期/损坏/跨租户，不按 status 过滤，done 行被当「可恢复中断」加载。 |
| D-002 | P2 | UNIQUE(conversation_id, message_id) 在 SQLite 对 NULL 不去重；应用层 SELECT-then-upsert 仅单线程安全。 | message_id 为 NULL 时唯一约束不生效。 |
| P2-1 | P2 | plan §2 承诺 4 条反例未落地（persist_failure / l2_write_unauthorized / event_contract / concurrent_conflict）；4 内置工具 golden 仅守 calculator。 | 测试缺口。 |
| P2-2 | P2 | 正向续跑用例用 `message_id="msg-1"`，未用生产绑定，恰好没盖住 P1-1。 | 测试绑定与生产不一致。 |

## 2. 修复设计（约束：不改 4 内置工具对外行为、不新增 AgentEvent 类型、未注入 legacy 不变）

### 2.1 P1-1 根因修复（两部分，同时上）

**Part A — status 过滤（直击根因）**：
`AgentStateService.load_active` 只恢复「中断中」状态行：`row.status in {"running","suspended"}`
才返回数据；`done/error` 终态行 **直接 discard 并返回 None（按全新 run）**。
→ 终态行绝不被加载续跑；新 run 完成后终态行不再影响后续消息；表不堆积。

**Part B — 绑定真实 message_id（使唯一约束真实生效 + 精确跨消息隔离）**：
核对 `app/services/quota.py:147`：用户 Message 行在 pipeline 执行**之前**已 `session.add`
并 `commit`，其 `id` 可用（UUID 默认在 flush 时生成）。故：
- `accept_user_message` 改为返回 `tuple[Conversation, str]`（conversation + 用户消息行 id）；
- chat_service `_accept_user_message` 透传该 id；
- `chat()` / `chat_stream()` 解包后传给 `_wire_runtime(..., message_id=user_message_id)`；
- `state_binding["message_id"]` 绑真实用户消息行 id（非 NULL）。

最终绑定机制选择：**真实用户 Message 行 id**（非 NULL）。理由：
1. 它在 pipeline 执行前已建、id 可用，改动最小（quota 单函数返回值 + chat_service 接线）；
2. message_id 非 NULL → DB 级 UNIQUE(conversation_id, message_id) 真实生效，同时关闭 D-002；
3. 每条用户消息独占一行 checkpoint → 跨消息天然隔离（第二条消息 id 不同，`load_active` 查不到第一条行）；
4. 中断续跑语义收敛为「同一次用户消息」（ADR-0010 决定 3：不跨轮），与 Part A 状态过滤形成双保险。

NULL message_id 仅作为单元测试/降级兜底路径保留（service 层 `_find_row` Python 补偿），生产不再产生 NULL 行。

### 2.2 语义闭合（对照修复约束①-④）

- ① 终态 done/error 行绝不续跑：Part A。
- ② 同会话不同用户消息互不污染：Part B（不同 message_id）+ Part A（双保险）。
- ③ 中断工具链仍可续跑：bound 到同一 message_id 的第二次 run，`load_active` 命中 running 行 → apply_snapshot → 已完成工具指纹保留、不重跑不重复副作用。
- ④ DB 级唯一性对非 NULL message_id 真实生效（Part B）。

## 3. 回归测试（先红后绿；真实服务层 + 隔离库，非纯单元）

新增/扩展（文件 `tests/test_agent_state.py` 与新增 `tests/test_agent_state_resume.py`）：

| 用例 | 注入 | 断言业务行为 | 阶段 |
|---|---|---|---|
| `test_terminal_done_row_not_resumed` | save status=done 行后 load_active | 返回 None、该行被 discard；fresh state 不含 tool_results/指纹 | 先红后绿 |
| `test_multi_chat_messages_isolated_no_state_leak`（等价真实入口） | quota 建 conv+m1→跑 pipeline(echo) 写 done；再 quota 建 m2→新 pipeline run 起始 `_resume_state` | m2 的 state 无 echo 指纹、无 tool_results、无 m1 的 context；m2 合法 echo 调用真实执行（计数 1） | 先红后绿 |
| `test_resume_same_message_continues_not_restarting`（生产绑定） | quota 建 m1→手动写 running 行(echo 指纹)→新 pipeline bound(m1) 再发同一 echo | echo func 不重跑（skip）、副作用不重复 | 机制绿，改绑定路径 |
| `test_checkpoint_persist_failure_does_not_break_main_chain` | monkeypatch 使 save_checkpoint 抛错 | 主链 action loop 照常完成，回复产出，不抛异常 | 补 P2-1 |
| `test_l2_write_denied_for_unauthorized_role` | member 角色经 Executor/Registry 调 file_ops_write | 返回「拒绝」、不落盘、审计 allowed=false | 补 P2-1 |
| `test_agent_event_contract_unchanged_no_new_type` | run_stream 收集事件类型 | 类型集合 ⊆ {stage,token,tool,code_result,done,error} | 补 P2-1 |
| `test_concurrent_conflict_does_not_corrupt_other_rows` | 同绑定两次 save + 异绑定各一行 | 同绑定单行（upsert）；异绑定两行互不覆盖、他行不损坏 | 补 P2-1 |
| `test_all_builtin_tools_bound_legacy_observation_golden` | calculator/code_sandbox/get_current_datetime/web_fetch 逐工具比对 | bound.observation == legacy.run(call) 逐字节 | 扩展 golden |

## 4. 受影响验证范围

- 新增/修改测试文件全量；
- `pytest -k "agent or tool or chat"`（回归，含既有 chat 入口）；
- `ruff check . --no-cache`、`mypy app/`（conda 解释器，隔离库 data/test_ai_assistant.db）。

## 5. 修复后证据回填（2026-10-12）

### 5.1 红→绿记录

- 修复前先写测试并实跑：`test_terminal_done_row_not_resumed`、`test_terminal_error_row_not_resumed`
  **红**（load_active 返回了 done/error 行，断言 `is None` 失败）；其余多轮隔离/续跑/失败路径/事件契约/并发/golden 用例在真实 message_id 绑定下已绿。
- 修复实现：
  - `app/services/agent_state_service.py`：新增 `RESUMABLE_STATUS={running,suspended}`，`load_active`
    对非可恢复状态行直接 discard 并返回 None。
  - `app/services/quota.py`：`accept_user_message` 改返回 `tuple[Conversation, user_message_id]`。
  - `app/services/chat_service.py`：`_accept_user_message` 透传 id；`chat()`/`chat_stream()` 解包并把
    真实 `user_message_id` 传给 `_wire_runtime(..., message_id=...)`，`state_binding["message_id"]` 绑真实用户消息行 id。
  - `tests/test_rag_log_minimization.py:245`：monkeypatch 返回值适配为元组（受 quota 返回值变更影响）。
- 修复后：上述 2 个终态用例转绿；`tests/test_agent_state.py` + `tests/test_agent_state_resume.py` 合计 **21 passed**。

### 5.2 四组验证（conda 解释器 D:\DepTooL\anaconda3\envs\ai-assistant\python.exe，隔离库）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest tests/test_agent_state.py tests/test_agent_state_resume.py -q` | 0 | 21 passed |
| `pytest -k "agent or tool or chat or quota or rag_log" -q` | 0 | 279 passed / 0 失败（1505 deselected） |
| `ruff check . --no-cache` | 0 | All checks passed |
| `mypy app/` | 0 | Success, no issues in 199 files |

### 5.3 剩余未修复项

- D-003（实现说明与验证证据落盘）：按任务边界本阶段不写实现说明/关闭对账，留待独立验证与审查后。
- D-002 的并发孤儿行：生产 message_id 已非 NULL，UNIQUE 真实生效；NULL 路径仅作单元/降级兜底，并发保护未做多进程压测（代码走查已注明）。
- 根目录 `README.md`/`.env.example` 未同步 `AGENT_STATE_*`（已登记偏差，不越界修改）。
- 本阶段未 git 提交。
