# PWFL-004 实现说明：工作流失败告警与执行日志持久化

> 日期：2026-10-21
> 状态：in_review（本轮修复已通过本地验证，最终CI与用户业务接受待完成；不自行标done）
> 基线：HEAD=`12ef449`（含 FLOW-005 提交 `523e2c0`），分支 `codex/flow-003-scheduled-execution`
> 解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`；测试库 `data/test_ai_assistant.db`

## 1. 实际完成了什么

一次工作流执行最终失败时，落库一条可查询告警记录；同时每次执行生成一个执行级 trace_id，所有执行日志（开始 + 结束）关联该 trace_id，可按 trace_id 检索终态后保存的执行摘要；不是实时或逐次 attempt 的完整轨迹。

### 谁可以做什么
- **系统管理员 / 租户管理员**：通过数据层查询函数（`app/workflow/execution_log.py`）按租户、工作流、执行 ID、trace_id 查询失败告警与执行日志。
- **多租户隔离**：告警与日志均带 tenant_id，查询时按租户过滤；他租户查不到本租户数据。
- **普通成员**：本卡不新增 API/前端，查询能力由数据层函数提供，后续可通过 API 暴露。

### 成功与失败行为
- **成功执行**：产生 2 条执行日志（开始 + 结束），**不产生告警**。
- **失败执行**（重试耗尽 / 超时 / 桥接异常 / 创建者失效 / 租户不可用）：产生恰好 1 条告警 + 2 条执行日志。
- **重试过程**：一次执行内的多次尝试共享同一 trace_id；只在最终失败处写一条告警，不在每次 attempt 重复写。
- **日志/告警写入失败**：吞异常 + 日志记录，不阻断主执行流程。

## 2. 执行级 trace_id 方案

- **来源**：在 `WorkflowEngine._execute_admitted` 执行开始时生成 `uuid.uuid4().hex[:12]`（与 `app/debug/trace.py` 的 `_trace_id()` 同风格）。
- **贯穿方式**：trace_id 作为参数传入 `_attempts`，并用于所有日志条目和失败告警。同一次执行的开始日志、结束日志、告警均携带同一 trace_id。
- **重试归并**：trace_id 在执行开始时生成且不变，重试/退避过程中的所有日志自然归并到同一 trace。

## 3. 代码位置

| 文件 | 改动 |
|---|---|
| `app/audit/models.py` | 新增 `WorkflowExecutionAlert`（表 `workflow_execution_alerts`）和 `WorkflowExecutionLog`（表 `workflow_execution_logs`）。该模块已被 `app/core/database.py:46` side-effect import，`create_all` 幂等建表。 |
| `app/workflow/engine.py` | 导入新模型与 uuid；`_execute_admitted` 生成 trace_id；终端 commit 后批量写开始+结束日志、失败时写告警；新增 `_write_exec_log`/`_write_exec_logs`/`_write_failure_alert`/`_classify_failure_reason` 辅助方法；setup 异常路径也写日志+告警。 |
| `app/workflow/execution_log.py` | 新建。提供 `list_alerts`、`get_logs_by_trace_id`、`list_logs` 三个查询函数。 |
| `tests/test_workflow_execution_alert.py` | 新建。5 个用例：失败产生一条告警、日志按 trace_id 关联、重试耗尽只一条告警、跨租户隔离、成功无告警但有日志。 |

### 关键设计决策
- **日志/告警在终端 commit 之后统一写**：避免在执行中途引入额外 `session.commit()`，干扰既有事务计数测试（如 `test_terminal_commit_failure_keeps_consumed_slot`）。
- **`_attempts` 内不写日志**：保持原有事务边界不变，日志粒度为「开始 + 结束」两条，满足「执行日志与 trace_id 关联」的验收口径。
- **失败原因分类**：根据 `WorkflowExecution.error` 字段自动分类为 `retry_exhausted` / `timeout` / `owner_invalid` / `tenant_unavailable` / `cancelled` / `bridge_error` / `setup_error`。

## 4. 验证命令与退出码

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -m pytest tests/test_workflow_execution_alert.py -v` | 0 | 5 passed |
| `python -m pytest -k workflow --basetemp=data/pytest-tmp-pwfl004` | 0 | **155 passed**, 3053 deselected |
| `python -m ruff check .` | 0 | All checks passed |
| `python -m mypy app/` | 0 | Success: no issues found |
| `python -m pytest --basetemp=data/pytest-tmp-batch2-full` | 0 | **3205 passed, 3 skipped, 0 failed**（1050.34s） |

### 红绿证据
- **红**：模型未实现时，5 个测试全部 FAILED（ImportError: cannot import name 'WorkflowExecutionAlert'）。失败原因是缺失业务行为，非导入/框架错误。
- **绿**：实现模型、查询函数与引擎写入后，5 passed。

### 持久化与幂等证明
- 测试中通过全新 `Session(engine)` 查询告警与日志（非同一内存对象），证明数据已落库。
- `test_retry_exhausted_single_alert`：WORKFLOW_RETRY_MAX=2 时桥接调用 3 次，告警表中该 execution_id 仍只有 1 条。
- `test_success_no_alert_but_has_logs`：成功执行产生 0 条告警、≥2 条日志。

### 独立审查后修复（LOW-3 / LOW-4）

- **LOW-3（计划漂移）**：`app/workflow/execution_log.py` 的 `get_logs_by_trace_id` 增加可选 `tenant_id` 关键字参数；有 tenant_id 时按租户过滤，None 时不加约束（与 `list_alerts` / `list_logs` 模式对齐），满足 AGENTS.md §8 数据访问租户校验要求。
- **LOW-4（测试缺口）**：
  - `test_failure_creates_one_alert` 补充断言：告警的 `trace_id` 与该执行的日志 `trace_id` 一致（端到端关联验证）。
  - `test_cross_tenant_isolation` 补充 `get_logs_by_trace_id` 的跨租户断言：他租户 tenant_id 传入时查不到本租户日志，本租户 tenant_id 可查到。

修复后重新验证：`pytest tests/test_workflow_execution_alert.py -v` 5 passed；`pytest -k workflow` 155 passed；`ruff check .` 退出 0；`mypy app/` 退出 0。

## 5. 明确没做的事（非目标 / 缺口）

- **不接入外部告警平台**（邮件、Webhook、PagerDuty 等）——契约 non-goal。
- **不做告警规则配置界面、不做 API 路由、不做前端页面**——allowed_paths 不含 `app/api/`、`frontend/src/`。
- **不修改 `WorkflowExecution` 表**（`app/models/workflow.py`）——不在 allowed_paths；trace_id 通过独立日志表关联，不加列。
- **不与 FLOW-005 的 `WorkflowOwnerAlert` 合并**：两种告警语义独立（创建者失效 vs 执行失败），各写各的表。
- **不写每次 attempt 的细粒度日志**：当前只写「开始 + 结束」两条，满足验收口径；细粒度尝试日志为后续增强项。
- **不做告警已读/解决状态、不做告警聚合/去重**。
- **未改 `app/core/database.py`**：新模型加在 `app/audit/models.py`，已被 side-effect import 链覆盖。
- **未 push / merge / deploy**。

## 6. 回滚

回退本卡提交即可；`workflow_execution_alerts`、`workflow_execution_logs` 表残留不影响既有功能（旧代码不读不写）。


## 验收后安全修复

2026-10-21用户要求修复验收缺陷：新增日志仅保存受控status/reason，告警error_message由内部reason映射到稳定错误码，不复制异常原文。分类优先识别内部重试耗尽前缀，避免下游错误正文伪造timeout/cancel。原WorkflowExecution.error的既有存储不在新表修复范围，历史告警不会自动清理；不宣称全系统异常字段已脱敏。新正式Adversarial用例覆盖Bearer、API Key、含凭据URL、用户输入回显与伪造错误码。模型注释同步为终态后两条摘要，不再声称存在每次尝试日志。最终结果以修复交付报告为准。


关闭登记（2026-10-21）：用户明确接受并要求关闭；同业务候选11f36aa的Ubuntu Backend/Frontend/Agent official CI均success，当前737输入与最终完整回归匹配，关闭前独立163项通过。对应任务已done，详情见docs/reviews/2026-10-21-workflow-three-cards-close.md。上文待验收内容保留为历史时点记录，不代表当前仍有该阻断；原非目标与残余边界保持。
