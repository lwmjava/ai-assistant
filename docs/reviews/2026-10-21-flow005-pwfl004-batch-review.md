# 批次独立审查报告：FLOW-005 + PWFL-004（2026-10-21）

> 审查者：独立审查 Agent（未参与任何一卡实现）
> 会话：agent_mode 独立审查子任务；审查者 id `s_000ElyVXUxk`，父 Agent `o_000Eld8sB7B`
> 日期：2026-10-21（Asia/Shanghai UTC+8）
> 仓库：`E:\culture\SmartCustomerServiceSystem\ai-assistant`，分支 `codex/flow-003-scheduled-execution`
> 审查依据：`tasks.yaml` 原始契约（FLOW-005:8918–8971、PWFL-004:5290–5335）、`docs/plans/plan_v1_parent_expand_flow_alert_20261004.md` §2、`docs/plans/plan_v1_task_split_20261002.md` C12、`docs/plans/plan_batch_flow005_pwfl004_20261021.md`、`docs/ai-prompts/skills/module-batch-delivery/references/acceptance-gates.md`
> 审查原则：以原始契约为准，自行设计反例，不凑问题数；无问题维度如实写「通过」。

## 1. 代码指纹

| 卡 | 提交 SHA | 提交说明 | 关键文件 |
|---|---|---|---|
| FLOW-005 | `523e2c08ac81de0b33654bb23af92549deae3bf2` | `feat(workflow): record tenant-visible alert when workflow owner becomes invalid` | `app/audit/models.py`（WorkflowOwnerAlert）、`app/workflow/engine.py`、`app/workflow/scheduler.py`、`app/api/routes/workflow.py`、`frontend/src/{types/api.ts,api/workflows.ts,pages/Workflows.tsx}`、`tests/test_workflow_owner_alert.py` |
| PWFL-004 | `ae14103e735dd1f2107bce03c32415fec4b3bd0e` | `feat(workflow): alert on execution failure and persist execution logs by trace id` | `app/audit/models.py`（WorkflowExecutionAlert、WorkflowExecutionLog）、`app/workflow/engine.py`、`app/workflow/execution_log.py`（新）、`tests/test_workflow_execution_alert.py` |
| 批次计划 | `12ef449` | `docs(plans): add module batch delivery plan for workflow alerts` | `docs/plans/plan_batch_flow005_pwfl004_20261021.md` |

提交链（最新在上）：`ae14103` → `12ef449` → `523e2c0` → `962b49b`。审查者对两卡最终 diff 做了完整 `git show` 静态阅读，并在阶段二（工作区安静后）执行运行时验证。

## 2. 覆盖范围

- 契约对账（deliverables / acceptance / non_goals / 依赖 / 批准决定逐条对照 tasks.yaml 原文）。
- 权限与多租户隔离。
- 失败行为与幂等（一次失败一条告警；重试过程不重复写；告警与停用语义同事务）。
- 两种告警语义分离（创建者失效 vs 执行失败，是否独立表、互不替代）。
- trace_id 关联（PWFL-004）。
- allowed_paths 合规（两卡各自清单）。
- 兼容与回归（路由顺序、既有工作流 CRUD/执行历史、事务计数测试）。
- 测试证据质量（红→绿真实性、持久化从新会话读取、失败后副作用/幂等断言、跨用例）。
- 提交说明规范、实现说明如实登记未做项、tasks.yaml 终态。

## 3. 运行时验证证据（审查者亲自执行）

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（conda 环境 `ai-assistant`，Python 3.12.0）。

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -m pytest -k "suspended_owner" -v --basetemp=data/pytest-tmp-review-flow005` | **0** | 6 passed / 3202 deselected（FLOW-005 六条全过：disabled / moved_out / scheduler_deleted / idempotent / tenant_admin_lists / cross_tenant） |
| `python -m pytest -k "workflow" --basetemp=data/pytest-tmp-review-workflow` | **0** | **155 passed** / 3053 deselected（含 `test_workflow_execution_alert.py` 5 条、`test_workflow_owner_alert.py` 6 条，及既有 admission/cancel/retry/schedule/timeout 全部工作流测试） |
| `python -m ruff check .` | **0** | All checks passed |
| `python -m mypy app/` | **0** | Success: no issues found in 222 source files |

说明：
- 使用全新 `--basetemp` 目录，规避 Windows 固定 run 目录清理 `WinError 5` 基线既有问题（实现者已在基线 `962b49b` 复跑证明为环境问题，非本批回归）。
- 本审查者**未独立重跑无筛选全量 pytest**（约 1700s）；全量证据以实现者登记为准（FLOW-005：3200 passed；PWFL-004：3205 passed / 3 skipped / 0 failed，`--basetemp=data/pytest-tmp-batch2-full`）。本审查者独立复核了 workflow 专项 155 条 + suspended_owner 6 条 + ruff + mypy，全部退出 0。
- 一条既有 `SAWarning`（`test_workflow_timeout_cancel.py:207`，Workflow identity map flush 冲突）为既有警告、非失败、非本批引入。

## 4. FLOW-005 契约对账结论

| 原始要求（tasks.yaml:8947–8965 / 裁决 §2） | 结论 | 证据 |
|---|---|---|
| 失效停用时写一条本租户可查询告警 | 通过 | `engine.py:303`（首次转入停用态写）+ `scheduler.py:130`（owner None 写），与 `enabled=False/suspended_owner=True` 同事务 commit（engine:315、scheduler:142） |
| tenant_admin 在列表/详情看到工作流、原创建者、原因 | 通过 | `GET /workflows/alerts` 返回全字段；前端 `AlertsStrip` 展示工作流名/原因文案/原创建者/时间 |
| 禁用或移出租户后任务为 suspended_owner 且有可查询告警 | 通过 | engine reason 级联：`owner_deleted`/`owner_disabled`/`owner_moved_out`/`owner_invalid`；测试两条路径断言 |
| 本租户 tenant_admin 能查到；不要求外部接收人 | 通过 | 租户过滤镜像 `_list_stmt`（`role_enum != SYSTEM_ADMIN` 才按 `user.tenant_id` 过滤）；跨租户测试断言 iso-b 查不到 iso-a |
| non_goal：不发邮件 / 不做 Webhook / 不替代 PWFL-004 | 通过 | 独立表 `workflow_owner_alerts`；diff 无 SMTP/外发；与执行失败告警分表 |

关键正确性点（审查者重点验证）：
- **路由顺序**：`/alerts`（workflow.py:209）声明在 `/{workflow_id}`（221）之前，不会被路径参数吞掉。红证据显示重排前曾 404。
- **告警不被外层 rollback 回滚**：engine 在 `_attempts` 内先 `session.commit()`（315）再 return failed；外层 `_execute_admitted` 的 `session.rollback()`（247）只回滚未提交部分，已 commit 告警持久。API 用例以全新 TestClient（新 DB 会话）读取成功佐证。
- **幂等**：`suspended_owner` 全库只置 True、从不重置（grep 确认），`if not current.suspended_owner` 守卫 → 每工作流恰好一条告警；`test_not_duplicated` 连续两次 execute 仍为 1 条。
- **allowed_paths**：11 个文件全在 `app/workflow/ app/api/ app/audit/ frontend/src/ tests/ docs/ tasks.yaml` 内；未碰 `app/models/`、`app/core/database.py`（新表挂在已被 side-effect import 的 `app/audit/models.py`）、`.env`。
- **提交/文档**：commit msg 无卡号/ADR；tasks.yaml 终态 `in_review`（非 done）；实现说明如实登记前端未走真实浏览器路径。

## 5. PWFL-004 契约对账结论

| 原始要求（tasks.yaml:5311–5328 / C12） | 结论 | 证据 |
|---|---|---|
| 失败产生告警事件 | 通过 | engine 终端 commit 后 `_write_failure_alert`；`test_failure_creates_one_alert` 断言行数=1 且字段齐 |
| 一次失败产生一条可查询告警记录 | 通过 | 告警只在终端落库处写一次；`test_retry_exhausted_single_alert`（retry_max=2，bridge 调 3 次）仍只 1 条 |
| 执行日志可按 trace_id 检索 / 日志与 trace_id 关联 | 通过 | 执行开始生成 `uuid4().hex[:12]`，贯穿开始+结束日志与告警；`test_execution_logs_linked_by_trace_id` 断言同执行日志共享同一 trace_id 且 `get_logs_by_trace_id` 返回全部 |
| 不接入外部告警平台 / 不做规则配置界面（non_goal） | 通过 | diff 无外发调用；无新 API 路由、无前端（allowed_paths 本就不含 app/api、frontend） |
| allowed_paths | 通过 | 7 文件全在 `app/workflow/ app/audit/ tests/ docs/ tasks.yaml` 内；未碰 `app/models/workflow.py`、`app/core/database.py`、`.env`、`app/api/`、`frontend/` |

关键正确性点：
- **失败分类**：`_classify_failure_reason(row.error)` 按 error 字符串映射 `timeout/owner_invalid/tenant_unavailable/retry_exhausted/cancelled/bridge_error/setup_error`。
- **日志/告警写入不阻断主流程**：`_write_exec_logs`/`_write_failure_alert` 各自 try/except 吞异常 + rollback + log；在执行行终态 commit 之后写，失败不回滚已成功的执行状态。
- **事务计数兼容**：刻意在终端 commit 之后才写日志/告警，不在执行中途引入额外 commit，避免干扰既有 `test_terminal_commit_failure_keeps_consumed_slot` 等事务计数测试（155 条 workflow 测试全过佐证）。
- **告警语义分离**：`workflow_execution_alerts` 与 FLOW-005 的 `workflow_owner_alerts` 独立表。owner 失效时同一次执行同时产生「创建者失效告警」（FLOW-005）与「该执行失败告警 reason=owner_invalid」（PWFL-004）——两者语义不同（工作流被停用 vs 本次执行失败），合理且不互相替代。
- **trace_id 来源**：后台调度/引擎无 HTTP 请求上下文，故在 `_execute_admitted` 内生成执行级 uuid（与 `app/debug/trace.py` 同风格），合理。

## 6. 反例验证结果（R1–R13）

| # | 反例 | 结果 |
|---|---|---|
| R1 | `/workflows/alerts` 不被 `/{workflow_id}` 吞掉（返回 200 列表而非 404） | 通过（路由顺序正确；API 测试 200） |
| R2 | 跨租户隔离：他租户 tenant_admin 查不到本租户告警 | 通过（`test_api_cross_tenant_isolation`） |
| R3 | 本租户 tenant_admin 查到含 workflow_name/owner_id/reason | 通过（`test_api_tenant_admin_lists`） |
| R4 | 重复触发不产生重复告警（FLOW-005） | 通过（`test_not_duplicated`，两次 execute 仍 1 条） |
| R5 | 告警在新会话读取可见（持久化非内存对象） | 通过（API 全新 TestClient；PWFL-004 用 `Session(engine)` 新会话查询） |
| R6 | 调度器 owner 删除路径写 owner_deleted、owner_username=None | 通过（`test_deleted_by_scheduler`） |
| R7 | disabled→owner_disabled、moved_out→owner_moved_out、tenant_id 保持工作流租户 | 通过（两条 engine 测试断言） |
| R8 | system_admin 跨租户可见告警 | 未测（代码逻辑为不按租户过滤；契约未要求，非缺口） |
| R9 | `reason=owner_invalid` 内部一致性分支 | 未测（代码存在，属边界，非契约用例） |
| R10 | 手动 run owner=None → 提前 409 不写告警 | 通过（既有行为，scheduler 兜底删除路径；见 LOW-1） |
| R11 | diff 无邮件/Webhook 外发 | 通过 |
| R12 | allowed_paths：无 app/models、无 database.py、无 .env | 通过（两卡 stat 逐文件核对） |
| R13 | tasks.yaml in_review 非 done；commit msg 无卡号 | 通过（两卡均 in_review；commit msg 合规） |

## 7. 发现的问题（按严重度）

无阻断性问题。以下均为 LOW / 信息项：

- **LOW-1（FLOW-005，既有行为非本卡引入）**：手动 `run_workflow` 时 owner 行已删除，API 在 `routes/workflow.py:325` 提前返回 409「创建者已失效」，不写告警、不停用；删除路径仅由 scheduler cron 兜底。若 `WORKFLOW_ENABLED` 关闭且 owner 被删，tenant_admin 手动 run 只看到 409 文本，告警列表为空。字面验收只要求「禁用/移出租户」，删除由调度器覆盖，**不构成契约违反**，属覆盖缺口。
- **LOW-2（FLOW-005，已如实登记）**：前端仅 typecheck/test/lint/build 四门（退出 0），未走真实浏览器用户路径、未截图、未实际点击。按门禁 §3 记为待用户次日业务验收缺口。
- **LOW-3（PWFL-004）**：`get_logs_by_trace_id(session, trace_id)` 函数签名**不接受 tenant_id、不做租户过滤**；计划 §4 契约表曾声称「get_logs_by_trace_id 按 tenant_id 过滤」，实际未实现。当前无 API 路由暴露该函数（allowed_paths 不含 app/api），trace_id 为 48 位随机不可猜，即刻利用风险为零；但未来若加 API 包裹，调用方无法在该函数内做租户收敛，需在服务层补 `user.tenant_id` 过滤。`list_alerts`/`list_logs` 已支持可选 tenant_id。
- **LOW-4（PWFL-004 测试覆盖）**：无断言证明「告警的 trace_id == 同执行日志的 trace_id」；跨租户测试只覆盖 `list_alerts`，未覆盖 `list_logs`/`get_logs_by_trace_id` 的租户过滤；失败执行的日志条目数未断言（成功执行已断言 ≥2 条）。
- **LOW-5（两卡测试夹具卫生）**：FLOW-005 的 disabled/moved_out 测试经 engine 终端路径会顺带触发 PWFL-004 写 `WorkflowExecutionAlert`/`WorkflowExecutionLog`，但 FLOW-005 的 autouse 清理夹具只删 `WorkflowOwnerAlert`/`WorkflowExecution`/`Workflow`，未删 PWFL-004 两表；测试按文件字母序执行（execution_alert 先于 owner_alert），会话末尾由 PWFL-004 夹具之前的 FLOW-005 测试残留少量行。不影响断言正确性（断言按 workflow_id/tenant_id 精确过滤，155+6 全过），仅夹具卫生问题。
- **信息项**：`_write_exec_log`（单条版静态方法）为死代码，仅 `_write_exec_logs` 批量版被使用；ruff/mypy 未标记。
- **信息项**：`workflows:read` 权限含 member，故同租户 member 也能读本租户 owner 告警——与既有工作流读范围一致，契约只要求 tenant_admin 可见，未违规。

## 8. 裁决

| 卡 | 裁决 | 说明 |
|---|---|---|
| **FLOW-005** | **通过（有缺口需用户验收，非阻断）** | 契约全部兑现、幂等/隔离/路由/事务正确性经静态+运行时验证；LOW-1 为既有覆盖缺口、LOW-2 为前端真实浏览器路径待用户验收。tasks.yaml 保持 in_review，待用户 2026-10-22 业务与代码验收。 |
| **PWFL-004** | **通过（LOW-3/4/5 为非阻断改进项）** | 契约全部兑现（一次失败一条告警、日志按 trace_id 关联、幂等、语义分离）；`get_logs_by_trace_id` 缺租户过滤参数为 LOW 设计改进项，无 API 暴露故不构成即刻隔离漏洞。tasks.yaml 保持 in_review。 |

两卡均未达到可标 done 的条件（门禁 §4：等待用户业务验收），与「本批不自行标 done」一致。

## 9. 未独立验证项（如实登记）

- 全量无筛选 pytest（~1700s）未由本审查者重跑；采用实现者登记的 3205 passed 作为全量证据。本审查者独立复核 workflow 专项 155 + suspended_owner 6 + ruff 0 + mypy 0。
- FLOW-005 前端 `AlertsStrip` 真实浏览器渲染与交互（无浏览器自动化能力，交用户验收）。
- Linux Ubuntu Backend CI 为最终平台门禁；本审查仅在 Windows 本地执行，平台分支以 CI 为准（AGENTS.md §10）。
- LOW-3 的未来 API 化租户收敛属后续设计，不在本批范围。

## 10. 修复复核（第二轮）

实现者针对第一轮 LOW-3/4/5 提交两个修复，本审查者复核如下。

### 10.1 修复提交与范围核对

| 提交 | 说明 | 触碰文件 | 边界核对 |
|---|---|---|---|
| `9880c771502baf78bd4a8f30c2a00ee10cdab2c4` | `test(workflow): clean execution alert and log tables in owner alert tests` | `tests/test_workflow_owner_alert.py`、`docs/plans/implementation_flow_005_20261021.md` | 仅 FLOW-005 文件，未碰 PWFL-004 文件 ✓ |
| `beced5ce73b87f3a7014f35377848b6247446949` | `fix(workflow): filter execution logs by tenant id and strengthen alert tests` | `app/workflow/execution_log.py`、`tests/test_workflow_execution_alert.py`、`docs/plans/implementation_pwfl_004_20261021.md` | 仅 PWFL-004 文件，未碰 FLOW-005 文件 ✓ |

修复指向：
- **LOW-5（夹具卫生）→ `9880c77`**：`_clean_owner_alert` fixture 先收集本用例 `workflow.id`，再按 `workflow_id` 级联删除 `WorkflowExecutionAlert`/`WorkflowExecutionLog`/`WorkflowOwnerAlert`，最后删执行与工作流行。精准消除 FLOW-005 测试经引擎终端路径产生的 PWFL-004 两表残留。附实现说明补记。
- **LOW-3（`get_logs_by_trace_id` 缺租户过滤）→ `beced5c`**：函数签名新增可选 `tenant_id`，传入时按 `WorkflowExecutionLog.tenant_id` 过滤；不传则不过滤（系统管理员场景）。与 `list_alerts`/`list_logs` 的可选 tenant_id 模式一致。
- **LOW-4（测试覆盖）→ `beced5c`**：补两条断言——(a) `test_failure_creates_one_alert` 中断言 `alert.trace_id ∈ {log.trace_id}`（告警与日志 trace_id 端到端一致）；(b) `test_cross_tenant_isolation` 中用租户 B 的 `tenant_id` 查租户 A 的 trace_id 返回空、用租户 A 返回非空，闭环 `get_logs_by_trace_id` 跨租户隔离。

无越界改动：未碰 `app/models/`、`app/core/database.py`、`app/api/`、`frontend/`、`.env`；两卡文件互不交叉。

### 10.2 最终态复核命令与退出码（审查者亲自执行）

解释器同上；全新 `--basetemp`。

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -m pytest -k "suspended_owner" -v --basetemp=data/pytest-tmp-review2-flow005` | **0** | 6 passed / 3202 deselected |
| `python -m pytest -k "workflow" --basetemp=data/pytest-tmp-review2-workflow` | **0** | **155 passed** / 3053 deselected |
| `python -m ruff check .` | **0** | All checks passed |
| `python -m mypy app/` | **0** | Success: no issues found in 222 source files |

修复后 LOW-3/4/5 对应测试断言均随 155 条 workflow 测试一并通过。

### 10.3 最终代码指纹

| 卡 | 最终 SHA（基础 + 修复） |
|---|---|
| FLOW-005 | 基础 `523e2c0` + 夹具修复 `9880c77` |
| PWFL-004 | 基础 `ae14103` + tenant 过滤与测试增强 `beced5c` |

### 10.4 每卡最终裁决

| 卡 | 最终裁决 |
|---|---|
| **FLOW-005** | **通过（可交付用户业务验收）**。LOW-5 已修复；剩余 LOW-1 为既有覆盖缺口（非本卡引入）、LOW-2 为前端真实浏览器路径待用户验收。tasks.yaml 保持 `in_review`。 |
| **PWFL-004** | **通过（可交付用户业务验收）**。LOW-3/4 已修复（`get_logs_by_trace_id` 支持租户过滤并补端到端 trace_id 与跨租户测试）。tasks.yaml 保持 `in_review`。 |

第一轮列出的 LOW-3/4/5 已闭环；LOW-1/2 为如实登记的既有/待验收项，不阻断。两卡均不自行标 done，等待用户 2026-10-22 业务与代码验收。
