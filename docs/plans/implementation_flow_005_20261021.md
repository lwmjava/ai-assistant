# FLOW-005 实现说明：创建者失效 → 本租户可查询告警

> 日期：2026-10-21
> 状态：in_review（等待用户 2026-10-22 业务与代码验收；不自行标 done）
> 基线：HEAD=`962b49b`，分支 `codex/flow-003-scheduled-execution`
> 解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`；测试库 `data/test_ai_assistant.db`

## 1. 实际完成了什么

创建者（owner）被禁用、移出租户或删除时，工作流被自动停用（既有 `suspended_owner=True, enabled=False` 行为保留），同时**同事务落库一条本租户可查询告警**。本租户具备 `workflows:read` 的用户（含 tenant_admin）可通过 `GET /api/v1/workflows/alerts` 查到；前端工作流页顶部展示告警条。

### 谁可以做什么
- 系统管理员：跨租户查看全部告警。
- 租户管理员 / 同租户成员（既有 `workflows:read` 权限）：仅看到本租户告警。
- 他租户 tenant_admin：查不到本租户告警（多租户隔离成立）。

### 成功与失败行为
- 成功：owner 失效 → 工作流停用 + 恰好一条告警（含工作流名/ID、原创建者 ID/用户名快照、原因）。
- 重复触发：已 `suspended_owner=True` 的工作流不会重复落告警（幂等守卫）。
- 告警写入失败不阻断主流程：写入与停用置位在同一事务；若告警写入异常会随事务回滚（与既有停用语义一致，不吞异常）。

## 2. 代码位置

| 文件 | 改动 |
|---|---|
| `app/audit/models.py` | 新增 `WorkflowOwnerAlert` 表（`workflow_owner_alerts`）：tenant_id/workflow_id/workflow_name/owner_id/owner_username/reason/detail + TimestampMixin。该模块已被 `app/core/database.py:46` side-effect import，`create_all` 幂等建表，无需改 database.py。 |
| `app/workflow/engine.py` | `_attempts` 复核身份失效分支：仅首次转入停用态时按 actor 状态判定 reason（`owner_disabled`/`owner_moved_out`/`owner_deleted`/`owner_invalid`）并写告警，再照旧置 `enabled=False, suspended_owner=True` 同事务 commit。 |
| `app/workflow/scheduler.py` | `_trigger` 的 `owner is None` 分支：置位前写 `owner_deleted` 告警。 |
| `app/api/routes/workflow.py` | 新增 `WorkflowAlertOut` 响应模型与 `GET /workflows/alerts`（声明在 `/{workflow_id}` 之前避免被路径参数吞掉）；system_admin 跨租户，其余按 `tenant_id` 过滤；按 created_at 倒序。 |
| `frontend/src/types/api.ts` | 新增 `WorkflowAlertOut`。 |
| `frontend/src/api/workflows.ts` | 新增 `useWorkflowAlerts()` 与 query key。 |
| `frontend/src/pages/Workflows.tsx` | 新增 `AlertsStrip`：存在告警时在页首展示「工作流名 / 原因文案 / 原创建者 / 时间」。 |
| `tests/test_workflow_owner_alert.py` | 6 个用例（禁用/移出租户/调度器删除/幂等/本租户可查/跨租户隔离）。 |

## 3. 验证命令与退出码

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -m pytest tests/test_workflow_owner_alert.py -v` | 0 | 6 passed |
| `python -m pytest -k suspended_owner -v`（本卡 evaluation） | 0 | 6 passed, 3197 deselected |
| `python -m pytest`（无筛选全量，`--basetemp=data/pytest-tmp/full-clean`） | 0 | **3200 passed, 3 skipped, 0 failed**（1767.82s） |
| `python -m ruff check .` | 0 | All checks passed |
| `python -m mypy app/` | 0 | Success: no issues found in 221 source files |
| `cd frontend; npm ci` | 0 | 381 packages |
| `npm run typecheck` | 0 | tsc --noEmit 无错 |
| `npm test` | 0 | 18 files / 167 tests passed |
| `npm run lint` | 0 | eslint 无错 |
| `npm run build` | 0 | ✓ built in 19.34s |

### 红绿证据
- 红：补模型但未写写入点/API 时，`tests/test_workflow_owner_alert.py` 6 failed——引擎/调度器路径告警数为 0；`GET /workflows/alerts` 被 `/{workflow_id}` 吞掉返回 404「工作流不存在或无权访问」。失败原因是缺失业务行为，非导入/框架错误。
- 绿：实现写入点与 API 后 6 passed。

### Windows basetemp 说明（既有环境问题，非本卡回归）
- 首次无 `--basetemp` 跑全量时，conftest 固定的 `data/pytest-tmp/run` basetemp 因 Windows 文件句柄占用，会话结束清理报 `PermissionError WinError 5`，被 pytest 计为大量 error。
- 已用 `git stash` 在基线 `962b49b` 复跑同一用例，错误完全相同，证明为基线既有 Windows 临时目录清理问题。
- 改用全新 `--basetemp` 后全量 3200 passed / 0 failed（上表）。该问题不属本卡范围，且按 AGENTS.md §10 平台分支以 Linux Ubuntu Backend CI 为最终门禁。

### 持久化与幂等证明
- API 用例以全新 TestClient 请求（新会话）读取告警，非同一内存对象。
- `test_suspended_owner_alert_not_duplicated`：连续两次 execute，告警仍为 1 条。

### 夹具卫生补记（LOW-5，审查后修复）
- 本卡测试经引擎终端路径会触发 PWFL-004 的 `workflow_execution_alerts` / `workflow_execution_logs` 两表写入；原 teardown 只清 `workflow_owner_alerts` / `workflow_executions` / `workflows`，会在共享测试库残留这两表的行。
- 已在 `tests/test_workflow_owner_alert.py` 的 `_clean_owner_alert` fixture 中：先收集本用例产生的 `workflow.id`，再按 `workflow_id` 级联删除 `WorkflowExecutionAlert`、`WorkflowExecutionLog`、`WorkflowOwnerAlert`，最后删执行与工作流行。只清理本用例的 workflow 行，不触碰其他用例/其他卡的数据。
- 复跑：`pytest tests/test_workflow_owner_alert.py -v` 6 passed；`pytest -k suspended_owner` 6 passed；`ruff check .` 退出 0；`mypy app/ tests/test_workflow_owner_alert.py` 退出 0。

## 4. 明确没做的事（非目标 / 缺口）

- 不发邮件、不做 Webhook/外发通知（契约 non-goal）。
- 不实现执行失败告警（PWFL-004 范围，本卡未建任何执行失败告警机制）。
- 不做告警已读/解决状态、不做告警规则配置界面。
- 未改 `app/models/`、未改 `app/core/database.py`、未触碰 `.env`/生产数据。
- **前端页面未走真实浏览器用户路径**：仅通过 typecheck/test/lint/build 四门（退出 0）与构建产物验证；无浏览器自动化能力，未截图、未实际点击。`AlertsStrip` 的真实渲染与交互交由用户次日业务验收。
- 未 push / merge / deploy。

## 5. 回滚

回退本卡提交即可；`workflow_owner_alerts` 表残留不影响既有功能（旧代码不读不写）。
