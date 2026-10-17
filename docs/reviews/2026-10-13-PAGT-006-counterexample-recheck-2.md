# PAGT-006 反例审查复验结论（第二轮）

- 复验日期：2026-10-13
- 复验人：入口级反例审查 Agent（**未参与实现**），会话身份 `s_000EtlRcF7j`
- 复验对象：`docs/reviews/2026-10-12-PAGT-006-counterexample-review.md` 中 D-001（P1 跨消息污染）与 D-002（P2 NULL 唯一性）的修复
- 修复依据：`docs/plans/plan_pagt_006_review_fixes_20261012.md`（实现者摘要：Part A `load_active` 只恢复 running/suspended，终态 done/error 行 discard；Part B `quota.accept_user_message` 返回 `(conversation, user_message_id)`，`chat_service` 注入真实 message_id；新增 `tests/test_agent_state_resume.py`）
- 等价审查说明：仓库内不存在 `code-review-quality` 技能；本次复验从真实 `ChatService.chat` 入口自行构造反例并实跑，未调用任何不存在的技能。

---

## 1. 修复后代码核对

| 修复点 | 代码位置 | 核对结果 |
|---|---|---|
| Part A：终态行不恢复 | `app/services/agent_state_service.py:25-26, 101-108` | `RESUMABLE_STATUS = frozenset({"running","suspended"})`；`load_active` 在 106-108 行对 `row.status not in RESUMABLE_STATUS` 直接 discard 并返回 None。✓ |
| Part B：quota 返回元组 | `app/services/quota.py:120, 156` | 返回类型改为 `tuple[Conversation, str]`；156 行 `return conversation, user_message.id`。✓ |
| Part B：chat_service 透传 | `app/services/chat_service.py:319, 390, 477-486` | `chat`/`chat_stream` 解构 `conv, user_message_id = self._accept_user_message(...)`；`_accept_user_message` 返回 `tuple[Conversation, str]`；`_wire_runtime` 签名增 `message_id: str` 并注入 `state_binding["message_id"]=message_id`（非 None）。✓ |
| 既有调用方适配 | `tests/test_quota.py:426`（不接收返回值，try/except 吞掉元组）、`tests/test_rag_log_minimization.py:245`（monkeypatch 返回 `(conv, "user-msg-log-regression")` 元组） | 两个既有调用方均已适配元组返回，无破坏。✓ |

---

## 2. D-001 复验：同会话两轮隔离

- **入口**：`ChatService.chat(session, user, message, conversation_id)` 真实非流式入口。
- **注入**：脚本 LLM 第一轮行动阶段返回 calculator 工具调用；第二轮同会话直接回答。
- **期望**：第二轮是全新 run，state 不含第一轮的 plan/tool_results/fingerprints/round_no。
- **实际**：

| 轮次 | msg_id | status | round_no | tool_results |
|---|---|---|---|---|
| 第一轮（"请计算 1+1"） | `3523a4c6...` | done | 1 | `["[calculator] 参数={'expression': '1+1'} 结果=2"]` |
| 第二轮（"second-turn 直接回答"） | `88801199...`（**新行**） | done | 0 | `[]` |

- **关键判定**：
  - 两轮是**各自独立的行**（msg_id 不同），第二轮为新 INSERT，不是覆盖第一轮的行。✓
  - 第二轮 `tool_results=[]`，**不含 calculator 残留**。✓
  - 第二轮 `round_no=0`，未继承第一轮的 1。✓
  - 第二轮是 done 行；即使它被下次 run 当 checkpoint 读取，Part A 的 `load_active` 也会因 `status=done not in RESUMABLE_STATUS` 直接 discard，不会恢复。✓
- **脚本误报说明**：复验脚本曾判 [FAIL]"第二轮 plan 仍继承第一轮"——这是**判定脚本误报**，不是代码缺陷。第二轮 `plan='用户想算一道数学题。'` 与第一轮相同，是因为脚本 LLM 对"理解"阶段固定返回该文本（不基于 user_input 生成），而非从第一轮 checkpoint 继承。继承与否的真实证据是 tool_results（[] vs [calculator 观测]）和 round_no（0 vs 1），二者均证明第二轮是全新 run。
- **结论**：**D-001 已闭环**。

---

## 3. D-002 复验：message_id 非 NULL + UNIQUE 真实生效

- **入口**：真实 `ChatService.chat` 跑完两轮后，直接 SQL 操作隔离测试库。
- **实际**：
  - 两轮后 `agent_run_states` 表中所有行的 `message_id` 均为真实用户消息行 id（非 NULL）；`message_id 为 NULL 的行数 = 0`。✓
  - 直接 SQL `INSERT` 重复 `(conversation_id, message_id)`（取第一轮行的同主键对）被 `sqlite3.IntegrityError: UNIQUE constraint failed: agent_run_states.conversation_id, agent_run_states.message_id` 拒绝。✓
- **结论**：**D-002 已闭环**。UNIQUE 约束在 DB 级真实承担去重职责，不再依赖 service 层 Python 补偿。

---

## 4. 既有反例复跑

| 反例 | 结果 |
|---|---|
| AGENT_STATE_PERSIST_ENABLED=false 零行写入 | 行数=0 ✓ |
| bound/legacy 观测逐字节一致 | `[calculator] 参数={'expression': '1+1'} 结果=2` 两路径一致 ✓ |
| 伪造 tenant_id 参数被拒 | 返回"调用未授权" ✓ |
| web_search 参数打码 | `[redacted]` ✓ |
| code_sandbox 路径净化 | `<sandbox>` ✓ |
| 流式 AgentEvent 类型不新增 | 收集到 stage/token/done，无新增类型 ✓ |
| 核心回归 `pytest tests/test_agent_state.py tests/test_agent_state_resume.py tests/test_tool_executor.py tests/test_pipeline_budget.py tests/test_chat.py tests/test_tool_audit.py tests/test_quota.py` | **82 passed**（含新增 test_agent_state_resume.py 8 用例）✓ |

---

## 5. 新问题排查

- **quota 返回元组对既有调用方的适配**：
  - `app/services/chat_service.py:319,390` 已解构为 `conv, user_message_id`。✓
  - `tests/test_quota.py:426` 不接收返回值（try/except 吞掉），元组返回不影响。✓
  - `tests/test_rag_log_minimization.py:245` monkeypatch `_accept_user_message` 返回 `(conv, "user-msg-log-regression")` 元组，已适配。✓
- **chat_service 接线正确性**：`_wire_runtime` 签名增 `message_id: str`，chat/chat_stream 两处调用点均传入 `user_message_id`；`state_binding["message_id"]` 为真实字符串。✓
- **未发现新引入的破坏**。

---

## 6. 总体结论

| 原缺陷 | 级别 | 复验结果 |
|---|---|---|
| D-001 跨消息状态污染 | P1 | **已闭环**：两轮独立行、第二轮 tool_results=[]/round_no=0、终态行不恢复 |
| D-002 NULL message_id 去重失效 | P2 | **已闭环**：message_id 非 NULL、UNIQUE 约束 DB 级生效（直接 SQL 插重复被 IntegrityError 拒绝） |
| D-003 验证证据文件缺失 | P2 | **已闭环**：`docs/reviews/2026-10-12-PAGT-006-verification.md` 已存在（17KB+） |
| D-004 .env.example/README 未同步 | P3 | 已登记为可接受偏差，非阻断 |

**结论：通过。** D-001 与 D-002 均已修复并独立复验通过；既有反例不回归；核心回归 82 passed。PAGT-006 可按既定流程进入关闭对账。
