# 批次计划：FLOW-005 + PWFL-004（2026-10-21）

> 状态：批次执行中
> 技能：`module-batch-delivery` v1.0.0（docs/ai-prompts/skills/module-batch-delivery/SKILL.md 及 references/acceptance-gates.md 已读取）
> 授权来源：用户指令「使用此技能，启用多agent依次实现FLOW-005，PWFL-004，实现时文档先落盘，并按照计划实现，我明天在做业务和代码验收」
> 执行模式：多 Agent 按依赖串行；实现 / 验证 / 独立审查角色分离；主 Agent 负责编排与最终整合验收
> 基线：工作区干净，HEAD=`962b49b`（feat(workflow): enforce shared concurrency and scheduled execution idempotency），分支 `codex/flow-003-scheduled-execution`

## 1. 授权卡清单与契约出处

| 卡 | 标题 | 现值 | 终态 | 优先级 | tasks.yaml 契约位置 | 依赖 | 前置核对 |
|---|---|---|---|---|---|---|---|
| FLOW-005 | 创建者失效：可查询告警通知 tenant_admin | backlog | **in_review（不得 done）** | P1 | 第 8918–8971 行 | FLOW-002 | FLOW-002 done（tasks.yaml:3650，status=done） |
| PWFL-004 | 工作流失败告警与执行日志持久化 | backlog | **in_review（不得 done）** | P2 | 第 5290–5335 行 | PWFL-001、NFR-002 | PWFL-001 done（tasks.yaml:5104，status=done；关闭复核 docs/reviews/2026-10-20-pwfl-001-closure-recheck.md）；NFR-002 done（tasks.yaml:4784，status=done） |

### 契约快照（以 tasks.yaml 原文为准，实现不得弱化）

**FLOW-005**
- 问题：创建者失效会 `suspended_owner` 停用任务，但没有本租户可查询告警，tenant_admin 不易发现。
- 事实依据：产品需求 §5.6（docs/product/项目产品需求方案.md）；2026-10-04 裁决为「可查询告警，不发邮件」（docs/plans/plan_v1_parent_expand_flow_alert_20261004.md §2，方案 1）。
- deliverables：① 失效停用时写一条本租户可查询告警；② tenant_admin 能在列表或详情看到工作流、原创建者、原因。
- acceptance：① 创建者禁用或移出租户后任务为 `suspended_owner` 且有可查询告警；② 本租户 tenant_admin 能查到该告警；不要求外部接收人。
- non_goals：不发邮件；不做 Webhook 外发；不替代 PWFL-004 执行失败告警。
- evaluation：`pytest -k suspended_owner -v`。

**PWFL-004**
- 问题：执行失败无人知晓，排查缺少完整轨迹。
- 事实依据：2026-10-02 定为 1.0 验收口径「一次失败产生可查询告警记录；不要求写明外部接收人」（docs/plans/plan_v1_task_split_20261002.md C12）。
- deliverables：① 失败产生告警事件；② 执行日志可按 trace_id 检索。
- acceptance：① 一次失败产生一条可查询告警记录；② 执行日志与 trace_id 关联。
- non_goals：不接入外部告警平台；不做告警规则配置界面。
- evaluation：`pytest -k 'workflow'`。

## 2. 依赖图

```text
FLOW-002(done) ──► FLOW-005 ──► [PWFL-004 与其无直接依赖；串行原因：共享 app/workflow/、app/audit/]
PWFL-001(done) ─┐
                ├─► PWFL-004
NFR-002(done)  ─┘
```

- 两卡共享 `app/workflow/`、`app/audit/`，**必须串行执行**（先 FLOW-005 后 PWFL-004），避免共享文件冲突与证据互相污染。
- 集合外：`PWFL-003` 已实现并提交 `962b49b`、状态 `in_review`，**非本批卡，不得改动其实现与证据**。

## 3. 批次非目标（边界声明）

- **不得创建或猜测 `PWFL-005`**：用户最初写「PWFL-005」随后明确更正为 `FLOW-005`；tasks.yaml 与 AGENTS.md 均无 PWFL-005。
- 两卡告警不得混同：FLOW-005 = 创建者失效（`suspended_owner`）告警；PWFL-004 = 执行失败告警。各自独立模型/记录，字段与语义互不替代。
- 不推送 / 不合并 / 不部署；不迁移真实数据；不重建真实索引；不触碰 `.env` 与生产数据。
- 本批两卡一律**不自行标 done**：自动验证 ≠ 用户业务接受；终态 `in_review`，等待用户 2026-10-22 业务与代码验收。
- 不引入未批准的新架构/依赖/外部服务；不实现 FLOW-005 非目标中的邮件/Webhook，不实现 PWFL-004 非目标中的外部告警平台/规则配置界面。

## 4. 允许范围（单卡各自遵守，不合并）

| 卡 | allowed_paths | forbidden_paths |
|---|---|---|
| FLOW-005 | `app/workflow/`、`app/api/`、`app/audit/`、`frontend/src/`、`tests/`、`docs/`、`tasks.yaml` | `.env`、`生产数据` |
| PWFL-004 | `app/workflow/`、`app/audit/`、`tests/`、`tasks.yaml` | `.env` |

- `app/models/` **不在两卡 allowed_paths 内**：新增告警/日志 SQLModel 表不得写入 `app/models/`，应在 `app/audit/models.py` 或 `app/workflow/` 内定义，并纳入 `app/core/database.py` 的 side-effect import（create_all 幂等建表）。
- `frontend/src/` 仅 FLOW-005 可改（tenant_admin 可见告警的列表/详情展示）；PWFL-004 无前端与 API 路径，其「可查询」落在数据层/仓储函数，由测试证明。

## 5. 权限与 Git 授权边界

- 沿用前序工作流卡（PWFL-001/002/003）模式：**仅本地提交，不 push / merge / deploy**。提交在当前分支 `codex/flow-003-scheduled-execution` 上进行。
- 提交说明只写实现的功能（英文 `feat(workflow): ...` 风格，对齐 CONTRIBUTING.md 与 tasks.yaml `commit_message_policy`）；**不写卡号、任务编号、ADR 编号、需求编号**。
- 命令解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（-m pytest / -m ruff / -m mypy）；pytest 用隔离测试库 `data/test_ai_assistant.db`。
- 公共路径（ChatService/API 身份/鉴权/执行链等）修改需无筛选全量 pytest；FLOW-005 涉及 API 契约与前端时必须跑前端四门。
- 单卡回滚只回该卡提交，不 reset/清空用户或前卡工作。

## 6. 所需工具 / 数据 / 已知入口与陷阱

### 工具
- 后端：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest`、`-m ruff check .`、`-m mypy app/`。
- 前端（FLOW-005）：`cd frontend && npm ci && npm run typecheck && npm test && npm run lint && npm run build`（Windows PowerShell 语法）。

### 已知入口（代码事实，2026-10-21 核对）
- `suspended_owner` 置位点：`app/workflow/engine.py:293`、`app/workflow/scheduler.py:129`（创建者失效自动停用已存在；FLOW-005 在此两处追加告警写入）。
- 工作流路由与模型：`app/api/routes/workflow.py`（`WorkflowOut` 已含 `suspended_owner`，第 97 行；执行历史 `ExecutionOut` 含 error/status）、`app/models/workflow.py`（`Workflow` / `WorkflowExecution`，app/models 不可改）。
- 建表机制：`app/core/database.py` side-effect import + `SQLModel.metadata.create_all`（幂等）+ `app.core.migration.auto_migrate`；新表须注册进导入链。
- trace_id：HTTP 请求上下文 `app/core/log_context.py`（get_trace_id / set_trace_id）；调试 trace `app/debug/trace.py`（`run_id = _trace_id()`）；结构化日志 `app/core/json_logging.py` 已带 trace_id。**后台调度/引擎执行无 HTTP 请求上下文，PWFL-004 须决定执行级 trace_id 的来源与落库方式**。
- 全库当前**无任何 Alert/告警模型**（2026-10-21 grep 核对）：两卡各自新建告警存储，互不混用。
- 审计事件：`app/audit/models.py`、`app/audit/logger.py`（`AuditAction`；`app/api/deps.audit_event`）——新告警若复用审计渠道须先确认其语义与租户可见性是否满足「tenant_admin 可查询」。

### 依据文档
- FLOW-005 裁决：`docs/plans/plan_v1_parent_expand_flow_alert_20261004.md`；产品需求 §5.6：`docs/product/项目产品需求方案.md`。
- PWFL-004 验收口径：`docs/plans/plan_v1_task_split_20261002.md` C12。
- 前置实现参考：`docs/plans/plan_flow_003_20261020.md`、`implementation_flow_003_20261020.md`、`implementation_pwfl_002_20261020.md`、`docs/adr/README.md`（ADR-0017/0018 相邻工作流上下文）。
- 必读入口：`docs/AI辅助开发迭代指导.md`、`docs/governance/agent-harness-engineering.md`、适用 `.cursor/rules/*.mdc`。

## 7. 批次台账（流程状态，非 tasks.yaml 枚举）

| 卡 | 计划落盘 | 失败测试(红) | 实现 | 验证 | 独立审查 | tasks.yaml 状态 |
|---|---|---|---|---|---|---|
| FLOW-005 | pending → | pending → | pending → | pending → | pending → | backlog → in_review |
| PWFL-004 | pending → | pending → | pending → | pending → | pending → | backlog → in_review |

## 8. 每卡闭环要求（对照 acceptance-gates.md）

1. **契约对账表**：从原始任务卡 deliverables / acceptance / non_goals / 依赖 / 批准决定建表，随实现计划落盘；不得只对照自撰计划。
2. **先红后绿**：先写能证明目标行为缺失的失败测试/案例并记录红，再最小实现；导入缺失或测试框架错误不单独证明业务失败。
3. **实现计划先落盘**（`docs/plans/plan_flow_005_20261021.md`、`docs/plans/plan_pwfl_004_20261021.md`）：目标、现状、方案、非目标、文件清单、风险、回滚。
4. **实现说明后落盘**（`docs/plans/implementation_flow_005_20261021.md`、`docs/plans/implementation_pwfl_004_20261021.md`）：实际完成什么、验证命令与结果、明确没做的事。
5. **独立审查**：非实现者读取原始卡、最终 diff、调用方与证据，自行设计反例，写入 `docs/reviews/2026-10-21-flow005-pwfl004-batch-review.md`（真实执行上下文、代码指纹、覆盖范围）。无真实独立审查不进入 in_review。
6. **验证**：ruff / mypy / 适用 pytest（`-k suspended_owner`、`-k 'workflow'`）实际执行并记录退出码；FLOW-005 另跑前端四门并记录退出码；公共路径改动跑无筛选全量 pytest。
7. **页面验收（FLOW-005）**：构建不代替页面验收；无浏览器/应用可运行时如实记录缺口，交由用户次日业务验收。
8. **持久化证明**：告警与执行日志落库后须从新会话/新查询读取验证（同一对象仍有值 ≠ 保存成功）；失败后的数据/副作用状态与幂等性需断言。

## 9. 模块验收与终点

- 模块验收：两卡共享 `app/workflow/`、`app/audit/` 的改动无冲突；合并后 `pytest -k workflow` 与 `-k suspended_owner` 全绿；两种告警（创建者失效 vs 执行失败）语义可区分、各自有独立可查询记录；FLOW-005 的 API/前端改动未破坏既有工作流 CRUD 与执行历史契约。
- 终点定义：两卡均达 in_review、实现说明 + 独立审查 + 证据落盘、tasks.yaml 状态与 progress 已登记，**等待用户 2026-10-22 业务与代码验收**；本批不声明 done。
- 存在阻塞时按 AGENTS.md 强制停止条件处理并如实报告，不借切换卡绕过。
