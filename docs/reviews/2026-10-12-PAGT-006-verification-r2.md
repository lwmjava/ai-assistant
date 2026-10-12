# PAGT-006 独立验证结论（第 2 轮：P1-1 修复后复验）

- 复验对象：PAGT-006 修复（`docs/plans/plan_pagt_006_review_fixes_20261012.md`）——对应第 1 轮验证（`docs/reviews/2026-10-12-PAGT-006-verification.md`）的 P1-1 阻断缺陷。
- 复验日期：2026-10-13
- 复验人：独立验证 Agent（与第 1 轮同一身份，未参与修复实现）
- 仓库：`E:\culture\SmartCustomerServiceSystem\ai-assistant`；基线 HEAD 仍为 `0b08c2ee`（修复全部为未提交工作区改动）
- 解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`；隔离测试库 `data/test_ai_assistant.db`
- 方法：不采信修复者摘要；P1-1 两个决定性场景用真实 `accept_user_message`（被改动函数）+ 生产绑定 + 隔离库独立复跑；四组命令独立重跑。
- 边界：只落本文件一个产物；未改任何源代码与测试。

---

## 1. 修复点代码核对（不采信摘要）

| 修复声明 | 代码实测 | 结论 |
|---|---|---|
| Part A：`load_active` 只恢复 running/suspended，done/error discard 返回 None | `agent_state_service.py:26` `RESUMABLE_STATUS=frozenset({"running","suspended"})`；`:106-108` `if row.status not in RESUMABLE_STATUS: discard(); return None` | 落地 |
| Part B：`accept_user_message` 返回 `(conversation, user_message_id)` | `quota.py`：`user_message = Message(...)`、`session.commit()`、`refresh(user_message)`、`return conversation, user_message.id`，返回注解 `tuple[Conversation, str]` | 落地 |
| chat_service 透传真实 message_id 注入 state_binding | `_accept_user_message` 返回元组；`chat()`/`chat_stream()` 解包 `conv, user_message_id`；`_wire_runtime(..., message_id=user_message_id)` 绑 `state_binding["message_id"]=message_id`（真实非空） | 落地 |
| 新增 tests/test_agent_state_resume.py（8 用例）+ test_agent_state.py 扩 2 终态用例 | git status 确认新文件存在；21 用例实跑绿（见 §3） | 落地 |

边界核对：本轮改动落在 `app/services/quota.py`、`app/services/chat_service.py`、`tests/test_rag_log_minimization.py`（适配 quota 返回值）、新增 `tests/test_agent_state_resume.py`——全部在 allowed_paths 内；无根目录 README/.env.example、无 `.env`/密钥。

---

## 2. P1-1 决定性场景独立复跑（真实 quota + 生产绑定）

复用第 1 轮冒烟方法，但改用**真实 `accept_user_message`** 建消息、绑真实 message_id：

### 场景 1：同会话两轮，第一轮 done 后第二轮应全新 run（不继承状态）

实测输出：

```
m1_id='e994fcf50c174eb1a1a5a853163efa60'   ← 非空真实消息行 id
第一轮 echo 执行次数=1；第一轮 done checkpoint 已落 m1_id
m2_id='b41bec47c41043589c4bdde700579842'   ← 与 m1_id 不同
第二轮 resume 后 tool_results=[]            ← 不继承
第二轮 resume 后 fingerprints=set()        ← 不继承
第二轮 resume 后 understanding=''          ← 不继承
第二轮 echo 执行次数=2（第一轮 1 → 第二轮真实执行 +1）
该会话 checkpoint 行数=2：
   message_id=e994fcf5 status=done
   message_id=b41bec47 status=running
>>> 第二轮是否全新 run（无污染）= True；echo 是否真实执行 = True
```

结论：跨消息污染已消除。第二轮 `_resume_state` 不继承第一轮的 tool_results/指纹/understanding/context；同一 echo(q=hello) 调用在第二条消息里**真实执行**（计数 1→2，不再被残留指纹静默 skip）。两行 checkpoint 各自绑定自己的 message_id，DB 级 UNIQUE 真实生效。

### 场景 2：同一条消息中断（running）后续跑仍成立

```
续跑 resume 后 fingerprints={'{"arguments":{"q":"hello"},"name":"echo"}'}
续跑 echo 执行次数 before=2 after=2   ← 指纹命中，已完成工具不重复执行
>>> 中断续跑是否恢复指纹 = True
```

结论：恢复面向「同一次用户消息」的中断续跑语义保留——running 行恢复指纹、已完成工具不重复执行、副作用不重复。与 ADR-0010 决定 3「不跨轮」一致。

**P1-1 闭环成立。** 两个决定性方向（跨消息隔离 + 同消息续跑）同时成立，第 1 轮的污染回归与工具静默 skip 不再复现。

---

## 3. 独立重跑（conda 解释器、隔离库）

| 命令 | 退出码 | 实际结果 |
|---|---|---|
| `pytest tests/test_agent_state.py tests/test_agent_state_resume.py -p no:cacheprovider --basetemp=data/pytest-tmp/pagt006-r2 -q` | **0** | **21 passed in 3.93s** |
| `pytest -k "agent or tool or chat or quota or rag_log" -p no:cacheprovider --basetemp=data/pytest-tmp/pagt006-r2 -q` | **0** | **279 passed, 1505 deselected in 45.26s** |
| `ruff check . --no-cache` | **0** | `All checks passed!` |
| `mypy app/` | **0** | `Success: no issues found in 199 source files` |

数字与修复者自述一致（21 / 279 / ruff 0 / mypy 0）。回归集已扩入 quota/rag_log（quota.py 返回值变更的受影响面），全绿。

---

## 4. P2 项闭环核对

| 第 1 轮 P2 | 落地情况 | 结论 |
|---|---|---|
| `test_checkpoint_persist_failure_does_not_break_main_chain` | `test_agent_state_resume.py:160`（monkeypatch save 抛错，主链 action loop 照常完成） | ✅ 落地 |
| `test_agent_event_contract_unchanged_no_new_type` | `:177`（run_stream 收集事件类型） | ✅ 落地 |
| `test_concurrent_conflict_does_not_corrupt_other_rows` | `:191`（同绑定单行 upsert、异绑定两行互不覆盖） | ✅ 落地 |
| `test_l2_write_denied_for_unauthorized_role` | 未新增专用 PAGT-006 用例，但该行为由既有 `tests/test_tool_audit.py::test_write_denied_for_member_role`（member 调 file_ops_write→拒绝、不落盘、审计 allowed=false）承载且绿 | 🟡 行为已覆盖（非本轮新增） |
| 4 内置工具 golden 覆盖 | `test_all_builtin_tools_bound_legacy_observation_golden` 覆盖 calculator(正常/缺参)、get_current_datetime、code_sandbox；**web_fetch 未入 cases** | 🟡 4/5 cases；web_fetch bound==legacy 已由本 Agent 第 1 轮不变量①冒烟独立实测通过 |
| 正向续跑用生产绑定（真实 message_id） | `conv_pair` fixture 用真实 Message 行 `.id`（m1/m2）；`test_multi_chat_messages_isolated_no_state_leak`、`test_resume_same_message_continues_not_restarting` 均绑真实 id | ✅ 闭环 |

新增的 `test_terminal_done_row_not_resumed`、`test_terminal_error_row_not_resumed`（test_agent_state.py:138/154）直接守门 Part A 的 status 过滤。

---

## 5. 三条不变量回归确认

- **① 4 内置工具 bound/legacy 逐字节相等**：修复未触碰 executor/base/builtin；`test_all_builtin_tools_bound_legacy_observation_golden` 随 21 用例绿；第 1 轮本 Agent 的 7 例独立比对（含 web_fetch）仍成立。✅
- **② AgentEvent 无新类型**：修复 diff 未新增任何 `AgentEvent(...)` 构造，且 `test_agent_event_contract_unchanged_no_new_type` 落地守门。✅
- **③ 未注入走原路径**：`_wire_runtime` 仍为可选注入，开关关时零行；quota 返回值变更不影响未注入 legacy 路径。✅

---

## 6. 残余观察（P3，非阻断）

1. `test_all_builtin_tools_bound_legacy_observation_golden` 的 cases 未含 web_fetch（缺参）；bound==legacy 已由本 Agent 独立冒烟实测，但 committed 用例未守 web_fetch 字符串。建议下轮顺手补入。
2. L2 写对 member 拒绝未新增 PAGT-006 专用用例，行为由 PAGT-002 既有 `test_write_denied_for_member_role` 承载。功能已覆盖，仅非本轮新增。
3. 生产 message_id 已非 NULL，UNIQUE 真实生效；NULL 路径仅作单元/降级兜底，未做多进程并发压测（修复计划 §5.3 已自行登记）。与 ADR-0010 单进程边界声明一致。

---

## 7. 总体结论

**通过（P1-1 闭环，P2 基本闭环）。**

- P1-1（跨消息 checkpoint 污染 + 工具静默 skip）：**已闭环**。本 Agent 用真实 `accept_user_message` + 生产绑定独立复跑，场景 1 跨消息无污染、合法同名同参工具真实执行；场景 2 同消息中断续跑语义保留。修复双保险（Part A status 过滤 + Part B 真实 message_id 绑定）同时成立。
- P2：4 条承诺反例中 3 条新落地、L2 写拒绝由既有测试承载、续跑测试改用生产真实绑定——**闭环**。仅 web_fetch golden 入 cases、L2 专用用例两条 P3 残余。
- 四组独立重跑（21 / 279 / ruff 0 / mypy 0）全绿，三条不变量保持。

**建议**：可按此关闭 PAGT-006 验收；两条 P3 残余（web_fetch 入 golden cases、L2 专用用例）可随手补，不阻断关闭。
