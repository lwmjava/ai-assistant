# FLOW-004 独立实施计划审查

日期：2026-10-20。审查身份：未参与 `docs/plans/plan_flow_004_20261020.md` 的撰写。本轮只审计划，未改产品代码、测试或 `tasks.yaml`，未运行 pytest。不构成实现验收或业务接受。

已读取 `tasks.yaml` 的 FLOW-004、`docs/plans/plan_v1_other_chapters_cards_20261003.md` 第 2.2 节、`app/workflow/scheduler.py` 的 `_is_scheduler_runnable` 与 `start_scheduler`、`app/api/routes/workflow.py` 的 `_require_enabled` 与列表路由、`frontend/src/pages/Workflows.tsx` 的 503 文案、`tests/test_openapi_coverage.py` 的 README 约束，并核对列表客户端、空列表文案与现有调度测试。

## 结论

**必须先改计划。** 交付边界与非目标对齐，响应头方案可以不新增路由。按当前写法实施，缺 croniter 时页面验收会失败，关闭开关与手动执行也没有被写成必过断言。

## 与原始契约

| 原始要求 | 计划 | 审查 |
|---|---|---|
| 打开且依赖齐全时定时路径可用 | 复用现有循环，不改最近到期算法 | 对齐。证据必须走 `start_scheduler`，这一点计划已写 |
| 未装 croniter 时页面写明只剩手动执行 | 列表响应头 `X-Workflow-Schedule: manual-only`，页面显示指定句 | 意图对齐，传递与空列表主文案会使验收失败，见阻断项 |
| 关闭时不自动跑定时 | 模式 `disabled`，不建循环，API 仍 503 | 行为描述对齐，测试未绑定 |
| 不做重试；不替代 FLOW-003 | 明确不改到点判定，不新增配置、依赖、迁移或表 | 无范围漂移 |

不新增 `GET` 路由是对的。`tests/test_openapi_coverage.py` 只要求 README「API 端点」表里的方法加路径能在 OpenAPI 中找到，并要求已注册的 `/api/v1` 路由带说明。新路径会迫使修改仓库根 `README.md`，而该文件不在本卡允许路径。现有 `GET /api/v1/workflows` 继续返回 JSON 数组，已有列表客户端不会因未知响应头损坏。

## 分项

**架构。** `schedule_mode()` 放在调度器、列表路由只写响应头，不另造执行入口，不需要新的 ADR。路由在权限依赖通过后、返回前写头，与「未登录或无读权限看不到模式」一致。不要把判定写进页面常量。

**兼容。** 开关关闭时整组接口仍是 503，页面标题保持「工作流引擎未启用」。这与 FLOW-002 一致，也不能把「只剩手动」写到 503 上：关闭时手动执行同样被拒绝。`scheduled` 与缺头时不显示新句子，避免把未启用说成还能手动执行。

**权限。** 模式只表示本进程开关和当前解释器能否导入 croniter，不带其他租户的工作流内容。系统管理员跨租户列表的 JSON 过滤保持原样。

**失败路径。** 三态 `disabled` / `scheduled` / `manual-only` 与现有 `_is_scheduler_runnable` 一致。共享判定必须继续沿用现在的宽捕获：导入失败记警告并不启动，不能让其他异常从 `lifespan` 里的 `start_scheduler` 打崩进程。第二次启动保持幂等。手动执行不经过 croniter，缺依赖时仍应能写入一条 `manual` 记录。开发与生产页面都请求同源 `/api/v1`（Vite 代理或后端托管构建产物），因此不必改 `app/main.py` 的 CORS；该文件也不在允许路径内。

**测试。** 打开且依赖齐全的到点证据可以沿用 `tests/test_workflow_schedule_integration.py` 里真正调用 `start_scheduler` 的用例。第 5 节点名的第一条失败测试只覆盖「列表还没有只剩手动信号」。现有用例没有断言关闭时 `start_scheduler` 不创建循环，也没有断言缺依赖时手动执行仍成功。公共 API 与调度链变更后的全量 pytest、ruff、mypy，以及前端四项检查，范围正确。

**性能。** 每次列表做一次进程内导入判断，不增加数据库扫描，不改变 tick 间隔。不能把这写成压测通过。

## 最小修正

只改计划，不扩大实现范围：

1. 写明现有 `api.get` 只返回 JSON、会丢掉响应头。列表查询必须在 `frontend/src` 内把 `X-Workflow-Schedule` 送进页面状态后再决定文案。只改 `Workflows.tsx` 的渲染分支、仍用现在的 `useWorkflows`，页面说明不会出现。
2. `manual-only` 时，空列表不得继续使用「引擎会按周期自动执行」。空列表和非空列表都显示：「未安装 croniter，定时不会自动运行，目前只剩手动执行。」`scheduled`、缺头和 503 保持现有文案。
3. 新增测试必须同时断言：关闭时 `start_scheduler` 不创建循环；缺 croniter 时响应头为 `manual-only`、不创建循环，且手动执行仍写入一条 `manual` 记录；打开且依赖齐全时，到点证据使用现有 `start_scheduler` 集成测试，不能只断言 `schedule_mode()` 等于 `scheduled`。
