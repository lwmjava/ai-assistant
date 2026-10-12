# PAGT-006 独立验证结论（独立验证 Agent，未参与实现）

- 验证对象：PAGT-006「独立 Tool Executor + State Manager 层（随工具扩展落地）」
- 验证日期：2026-10-12
- 验证人：独立验证 Agent（未参与本卡计划、实现与此前评审）
- 仓库：`E:\culture\SmartCustomerServiceSystem\ai-assistant`（Windows / PowerShell）
- 基线 HEAD：`0b08c2ee765d25b18e18a989fcdffb4ad3e35b30`（分支 `codex/pagt-tool-polish`）；实现全部为**未提交工作区改动**（符合计划「不 git 提交」）
- 解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`；隔离测试库 `data/test_ai_assistant.db`
- 方法：不采信实现者自验摘要，所有数字均为本 Agent 实际重跑；断言从原始契约（tasks.yaml PAGT-006 卡）、ADR-0010 与实施计划 §2 五类反例独立构造。
- 边界：只落本文件一个产物；未改任何源代码与测试。冒烟脚本置于 Agent 工作区（仓库外），仅读写隔离测试库。

---

## 1. 变更核对（git status / diff）

`git status --short` 实际结果与实现者自述变更清单逐条比对：

| 实现者自述 | git 实际 | 结论 |
|---|---|---|
| 新增 `app/agents/tools/executor.py` | `?? app/agents/tools/executor.py` | 一致 |
| 新增 `app/agents/state.py` | `?? app/agents/state.py` | 一致 |
| 新增 `app/models/agent_state.py` | `?? app/models/agent_state.py` | 一致（2026-10-12 已批准扩展 app/models/） |
| 新增 `app/services/agent_state_service.py` | `?? app/services/agent_state_service.py` | 一致 |
| 新增 `tests/test_tool_executor.py`、`tests/test_agent_state.py` | `??` 两者 | 一致 |
| 改 `app/agents/pipeline.py`、`tools/base.py`、`tools/contract.py`、`core/config.py`、`core/database.py`、`models/__init__.py`、`services/chat_service.py` | `M` 七者 | 一致 |
| docs/ | `M docs/adr/README.md` + `?? docs/adr/0010…` + `?? docs/plans/plan_pagt_006…` + 若干 review | 在 allowed_paths 内 |

边界核对：

- 无根目录 `README.md`、`.env.example` 改动（符合 plan §3 S10 修订：「AGENT_STATE_* 未同步根目录文件」为已记录偏差）。
- 无 `.env`、无密钥/连接串进入 diff。
- 未新增 `alembic/versions/` 迁移脚本——按 plan §4.3，dev/test 库经 `create_all` 幂等建表即可（本验证 `init_db()` 后 `agent_run_states` 表可正常查询，建表链路通）。
- 全部变更落在 `app/agents/`、`app/services/`、`app/core/`、`app/models/`（已批准扩展）、`tests/`、`docs/` 内，无越界。

**结论：变更文件清单与 allowed_paths 一致，无根目录/密钥污染。**

---

## 2. 独立重跑四组命令（conda 解释器、隔离库）

| 命令 | 退出码 | 实际结果（本 Agent 实跑） | 实现者自述 | 差异 |
|---|---|---|---|---|
| `python -m pytest tests/test_tool_executor.py tests/test_agent_state.py -p no:cacheprovider --basetemp=data/pytest-tmp/pagt006-verify -q` | **0** | **27 passed in 3.84s**（16+11） | 27 新测试全绿 | 一致 |
| `python -m pytest -k "agent or tool or chat" -p no:cacheprovider --basetemp=data/pytest-tmp/pagt006-verify -q` | **0** | **240 passed, 1536 deselected, 1 warning in 35.86s** | 240 passed | 一致 |
| `python -m ruff check . --no-cache` | **0** | `All checks passed!` | ruff 0 | 一致 |
| `python -m mypy app/` | **0** | `Success: no issues found in 199 source files` | mypy 0 | 一致 |

四组命令独立重跑全部通过，数字与实现者自述一致。**但通过不等于验收通过**——240 回归全绿是因为既有用例集未覆盖本卡新增接线的真实多轮路径（见 §6、§7）。

---

## 3. 三条不变量（独立断言）

### 不变量①：4 个内置工具 bound 路径与 legacy 路径观测文本逐字节相等 —— **通过**

独立脚本对 `default_tools()`（calculator/code_sandbox/get_current_datetime/web_fetch）逐工具比对 `Registry.run(call)`（未绑定 Executor）与 `ToolExecutor(reg).execute(call).observation`（经 Executor）：

```
[OK] calculator {'expression': '1+1'}
[OK] calculator {}                ← 缺参友好文案「未提供 expression 参数」
[OK] calculator {'expression': '1/0'}
[OK] get_current_datetime {}
[OK] get_current_datetime {'format': '%Y'}
[OK] code_sandbox {}              ← 缺参
[OK] web_fetch {}                 ← 缺 url
>>> bound==legacy 全部逐字节相等 = True
```

根源证据：`base.py` 新增唯一共享 helper `format_tool_observation(name, arguments, observation)`，legacy `Registry.run()` 与 `Executor.execute()` 都调它（diff 为旧内联拼装的逐字抽取，打码/路径净化规则一致），杜绝两份实现漂移。calculator 缺参友好文案在 bound 路径下仍由工具自身返回、未被 Executor 提前拦截（`test_executor_relaxes_validation_for_builtin_tools` 同证）。

### 不变量②：AgentEvent 流式契约不新增类型 —— **通过**

对 `app/` 全量 `AgentEvent(` 构造点 grep：现有类型集合为 `stage/token/tool/code_result/done/error`（pipeline）+ `conversation/sources/error`（chat_service）+ `subtask`（supervisor）。PAGT-006 对 `pipeline.py` 的 diff 仅新增 `_resume_state`/`_checkpoint` 两个方法及其调用，**未新增任何 `AgentEvent(...)` 构造**。SSE 前端消费契约不变。
（注：plan §2.4 承诺的 `test_agent_event_contract_unchanged_no_new_type` 用例实际未落地，本不变量由本 Agent 逐处 diff 核对替代验证。）

### 不变量③：未注入 Executor/StateManager 时走原路径 —— **通过**

`_wire_runtime` 仅在 `isinstance(pipeline, AgentPipeline)` 且 `tools is not None` 时注入；Supervisor/短路（fast path）路径不注入。`AgentPipeline.__init__` 新增 `executor/state_store/state_binding` 默认 `None`，`_execute_tool` 走 `if self.executor is not None … else self.tools.run(call)`，`_resume_state`/`_checkpoint` 首行即 `if self.state_store is None or not settings.AGENT_STATE_PERSIST_ENABLED: return`。`test_registry_run_unbound_legacy_path_unchanged`（calculator 2*3→`[calculator] … 结果=6`）绿。未注入时行为与 HEAD `0b08c2ee` 基线一致。

---

## 4. 失败边界逐条覆盖核对（对照 plan §2 五类反例清单）

| 类别 | plan 承诺用例 | 实际用例 | 实跑 | 覆盖结论 |
|---|---|---|---|---|
| §2.1 数值 | timeout/status=timeout、缺参 relaxed、坏类型记录、strict 硬拒绝 func 零调用、max_retries=0=1 次 | `test_executor_timeout_surfaces_status_timeout`、`test_executor_relaxes_validation_for_builtin_tools`、`test_executor_wrong_type_arg_recorded_in_metadata`、`test_executor_strict_contract_rejects_bad_args_without_calling_func`、`test_max_retries_zero_matches_legacy_attempts_one` | 全绿 | **5/5 覆盖** |
| §2.2 失败路径 | 幂等重试到 max、非幂等不重试、异常转观测不崩、损坏快照降级 discard、**checkpoint 写失败不阻断主链** | 前 4 条在；**`test_checkpoint_persist_failure_does_not_break_main_chain` 缺失** | 现有绿 | **4/5（缺 persist_failure）** |
| §2.3 权限租户 | 伪造身份拒绝、**L2 写对未授权角色拒绝**、跨租户不恢复、无行=全新 run | `test_forged_identity_argument_rejected`、`test_checkpoint_cross_tenant_not_restored`、`test_load_active_no_row_means_fresh_run`；**`test_l2_write_denied_for_unauthorized_role` 缺失**（L2 拒绝逻辑由 PAGT-002 既有审计用例部分承载） | 现有绿 | **3/4（缺 l2_write_unauthorized）** |
| §2.4 身份绑定 | 快照无密钥、过期忽略、**事件契约无新类型**、state 模块零 DB import、绑定不符 discard | `test_snapshot_contains_no_config_secrets`、`test_expired_snapshot_is_ignored`、`test_state_manager_module_has_no_db_import`、跨租户(=绑定不符)；**`test_agent_event_contract_unchanged_no_new_type` 缺失** | 现有绿 | **4/5（缺 event_contract；本 Agent 以 diff 核对替代）** |
| §2.5 回滚幂等 | upsert 单行、**并发冲突不污染他行**、4 工具 golden、关开关零行、unbound legacy、bound==legacy、重试聚合一条审计四键 | `test_checkpoint_upsert_is_single_row_not_duplicate`、`test_persist_disabled_writes_zero_rows`、`test_registry_run_unbound_legacy_path_unchanged`、`test_bound_executor_observation_golden_matches_legacy`、`test_logical_call_with_retries_writes_single_aggregated_audit_four_keys`；**`test_concurrent_conflict_does_not_corrupt_other_rows` 缺失；`test_builtin_tool_observation_golden_unchanged` 只覆盖 calculator，code_sandbox/get_current_datetime/web_fetch 的 golden 字符串未断言** | 现有绿 | **5/7（缺并发冲突 + 3 个内置工具 golden）** |
| §2.6 正向续跑 | 从 checkpoint 续跑不重启、不重复已完成工具 | `test_resume_continues_from_checkpoint_not_restarting` | 绿 | 覆盖，但**绑定 `message_id="msg-1"`（真实字符串），未用生产绑定 `message_id=None`**——见 §6 P1 |

**小结**：执行层（超时/重试/校验/伪造身份/聚合审计）反例覆盖扎实；状态层覆盖了损坏/过期/跨租户/单行/关开关，但 plan 承诺的 4 条反例（persist_failure、l2_write_unauthorized、event_contract、concurrent_conflict）未落地，4 内置工具 golden 仅 calculator 守了。属 plan 一致性缺口（P2，见 §8）。

---

## 5. 证据门禁核对

- 必交付项证据级别应为 **E2**（真实代码 + 隔离后端）：执行层用例直接 `asyncio.run(ToolExecutor(reg).execute(...))` 调真实执行器；状态层用例 `Session(engine)` 真实读写 `agent_run_states`。✅
- 持久化必须**新 Session/新进程**从隔离库读回断言：`test_checkpoint_written_at_tool_round_and_read_back_new_session`（:199-215）在写入 session 关闭后另开 `session2` 查询断言；upsert/跨租户/损坏用例均跨 Session。✅ 非同一对象保有值。
- 未发现以 E3/E4 支撑必交付项。
- **但**：生产绑定 `message_id=None` 下的持久化/续跑路径**无任何测试覆盖**——本 Agent 的冒烟脚本正是填补此缺口并发现了 §7 的 P1。

---

## 6. acceptance 逐条实测（重点）

### ① 工具调用统一经 Tool Executor（超时/重试/权限/审计）—— **通过**

- 代码：`_wire_runtime` 对真实 AgentPipeline 注入 `pipeline.executor = ToolExecutor(tools)`；`_execute_tool` 优先 `self.executor.execute(call).observation`。生产 chat 路径 executor 在链上。
- 超时包裹（`asyncio.timeout(contract.timeout_seconds)`）、幂等瞬时重试（`idempotent and transient and attempts<=max_retries`）、RBAC 组合委托 `_denied_reason`、审计组合委托 `_record_audit` 均在位。
- 审计聚合：`test_logical_call_with_retries_writes_single_aggregated_audit_four_keys` 证明含重试一次逻辑调用只写一条 TOOL_CALL、details 恒 `risk/allowed/reason/latency_ms` 四键、不落 arguments/密钥；`test_validation_conclusion_not_in_audit_details` 守门校验结论不进审计。

### ② 编排中间状态由 State Manager 管理、长会话可续 —— **在生产绑定下不成立（P1 阻断）**

正向续跑用例 `test_resume_continues_from_checkpoint_not_restarting` 本身绿，但它用 `message_id="msg-1"`（真实字符串）。**生产 `chat_service._wire_runtime` 实际绑定的是 `message_id=None`**（因 `quota.accept_user_message` 不返回用户消息行 id）。本 Agent 用与生产完全一致的绑定实测，结果见 §7。

### ③ 现有对话主链行为不回归（回归绿）—— **单轮绿、多轮同会话回归（P1）**

`pytest -k "agent or tool or chat"` 240 绿，但既有用例全部在**新建会话**跑单轮，无任何用例在**同一会话跑第二条用户消息**，因此 §7 的多轮污染回归未被回归集捕获。

---

## 7. 发现的缺陷

### 【P1-1｜阻断】跨消息 checkpoint 污染：生产绑定 `message_id=None` + `load_active` 不过滤 status，导致第二条消息继承上一条的中间状态、合法工具调用被静默跳过

**复现步骤（本 Agent 实跑，隔离库，退出码 0）：**

1. 建 tenant/user/conversation（真实 `AgentStateService` + `data/test_ai_assistant.db`）。
2. 模拟第一条消息完成：按生产 `run()` 终态，`save_checkpoint(conversation_id=conv.id, message_id=None, status="done", state_json=<StateManager(st1).to_json()>)`。
3. 第二条消息：全新 `AgentState`，按 `_wire_runtime` 同款 `state_binding={conversation_id:conv.id, message_id:None, …}` 注入 `state_store`，调用生产 `run()` 第一步 `_resume_state(fresh)`。

**实测输出（smoke1 / smoke2）：**

```
[A.行数] conv=… agent_run_states 行数 = 1  (message_id=None, status=done, round_no=1)
[A.污染检查] 第二条 fresh state 经 _resume_state 后：
   tool_results        = ["[calculator] 参数={'expression': '1+1'} 结果=2"]   ← 上一条泄漏
   understanding       = 'MSG1_UNDERSTANDING_STALE'                          ← 被覆盖
   context             = 'MSG1_CONTEXT_STALE'                               ← 覆盖了 ChatService 预设的 memory context
   executed_fingerprints= {'fp-calc-msg1'}                                  ← 上一条指纹泄漏
>>> 是否污染 = True
```

功能后果（smoke2，真实 `_run_action_loop`）：第二条消息里用户**再次请求同一个 echo(q=hello)**：

```
resume 后 executed_fingerprints = {'{"arguments":{"q":"hello"},"name":"echo"}'}
echo 执行次数 = 1   ← 第一条 1 次后，第二条未真正执行（应为 2）
第二条 tool_results 末尾 = '相同参数已执行过，请直接使用已有结果'
```

即：新消息的合法工具调用因上一条残留指纹命中 `_reject_tool_call`（pipeline.py:827-829）被判 `skip`，**func 不执行**，用户得到「相同参数已执行过」而非新结果。

**根因（两处叠加）：**
1. `chat_service._wire_runtime` 对每条消息都绑 `message_id=None`，checkpoint 实际按 `(conversation_id, NULL)` 单行 upsert（§8 场景 B 已验证 NULL upsert 不堆积，故无行膨胀问题）。
2. `AgentStateService.load_active` 只跳过「过期/损坏/跨租户」，**不按 `status` 过滤**；pipeline 终态写的是 `status="done"` 行且**从不 discard**。于是同会话下一条消息的 `_resume_state` 把上一条的 done 状态当成「可恢复的中断」加载。

**违背：**
- acceptance③「现有对话主链行为不回归」——多轮同会话对话行为被本卡改变（tool_results/context/指纹污染；特定情况下工具被静默跳过）。
- ADR-0010 决定3「恢复面向**同一次用户消息**的中断续跑…**不跨轮**；跨轮上下文由 Memory 承担」——实现实际跨消息续跑，且污染了本应交给 Memory 的上下文。
- acceptance②「长会话可续」的正向语义在生产绑定下并未正确达成（正向续跑只在测试用真实 message_id 时成立）。

**触发面**：任何 ≥2 条用户消息的会话（正常使用路径）。单消息会话不受影响；精确 skip 仅在新消息工具调用与上一条 name+arguments 完全相同时触发，但 tool_results/context 泄漏在每条多轮消息都发生。

**可操作修复建议（任选其一，推荐组合）：**
- (A) `load_active` 仅恢复「中断中」状态：只在 `row.status in {"running","suspended"}` 时返回，`done/error` 直接 `discard` 并按全新 run；终态成功后主动 `discard` checkpoint 行。（最小改动，直击根因）
- (B) `_wire_runtime` 把 `quota.accept_user_message` 返回的用户消息行 id 透出并绑定为真实 `message_id`，使 checkpoint 真正按 (conversation_id, message_id) 区分每条消息。
- (A)+(B) 同时上最稳；至少做 (A)。修复后须补一条**生产绑定 `message_id=None` 下的多轮回归**：第一条 done 后第二条 `_resume_state` 不得继承任何 tool_results/指纹/context。

### 【P2-1｜plan 一致性】承诺的失败边界反例未全部落地

plan §2 点名但实际缺失：`test_checkpoint_persist_failure_does_not_break_main_chain`、`test_l2_write_denied_for_unauthorized_role`、`test_agent_event_contract_unchanged_no_new_type`、`test_concurrent_conflict_does_not_corrupt_other_rows`；`test_builtin_tool_observation_golden_unchanged` 仅守 calculator，code_sandbox/get_current_datetime/web_fetch 的 golden 字符串未断言。不阻断功能，但与已批准 plan 承诺的反例覆盖不一致。

### 【P2-2｜acceptance② 覆盖缺口】正向续跑未在生产绑定下验证

`test_resume_continues_from_checkpoint_not_restarting` 用 `message_id="msg-1"`；生产绑定 `message_id=None` 的续跑/恢复路径无测试，而正是该路径存在 §7 P1。修复 P1-1 后须补生产绑定用例。

---

## 8. 补充核对（非缺陷）

- **NULL message_id 的 UNIQUE 不去重 → 行堆积？** 不会。`AgentStateService._find_row` 先按 (conversation_id, tenant_id) 查出行再在 Python 侧匹配 `row.message_id is None`，命中即更新。本 Agent 实测 3 次 `save_checkpoint(message_id=None)` 后该会话恰 1 行、`round_no` 取最新=3（upsert 更新而非堆积）。SQLite UNIQUE 对 NULL 不去重的隐患被应用层 SELECT-then-upsert 规避。
- **观测拼装同源**：bound/legacy 唯一实现 `format_tool_observation`，§3 不变量①逐字节证实。
- **无根目录 .env.example/README 同步**：按 plan S10 为已记录偏差，未越界修改，符合预期。

---

## 9. 总体结论

**不通过（存在 P1 阻断缺陷，须修复后复验）。**

- 工程静态面全部合格：变更边界一致、四组命令（27 / 240 / ruff 0 / mypy 0）独立重跑全绿、三条不变量（bound/legacy 逐字节相等、无新 AgentEvent 类型、未注入走原路径）成立、执行层失败边界反例扎实、证据达 E2、持久化跨 Session 读回。
- **但 acceptance②「长会话可续」与 acceptance③「主链不回归」在真实多轮对话路径上不成立**：生产绑定 `message_id=None` 叠加 `load_active` 不过滤 done 状态，导致同会话第二条消息继承上一条的 tool_results/context/指纹，并可能把新消息的合法工具调用静默判为「重复」跳过。240 回归绿是因为既有用例集不覆盖同会话多轮——这正是「单元全绿、真实路径破」的典型。

**放行前必做（阻断）**：
1. 修复 P1-1（推荐 `load_active` 只恢复 running/suspended + 终态 discard；并尽量绑定真实 message_id）。
2. 补生产绑定 `message_id=None` 下的多轮回归：第一条 done 后第二条 `_resume_state` 不得继承旧 tool_results/指纹/context。
3. 修复后重跑 §2 四组命令 + 新增多轮用例，本 Agent 复验 P1-1 关闭。

**建议顺带补（P2，非阻断）**：补齐 §7 P2-1 缺失的 4 条反例与 3 个内置工具 golden。
