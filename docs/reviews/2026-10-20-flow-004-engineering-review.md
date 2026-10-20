# FLOW-004 独立入口审查

日期：2026-10-20。审查身份：未参与 FLOW-004 的实现或计划撰写，只新建本文件。未改产品代码、测试或 `tasks.yaml`，未提交、未推送。不构成任务关闭、业务接受或可发布结论。

已读 `tasks.yaml` 的 FLOW-004、`docs/plans/plan_flow_004_20261020.md`、`docs/reviews/2026-10-20-flow-004-plan-review.md`，并核对 `app/workflow/scheduler.py` 的 `schedule_mode` 与 `start_scheduler`、`app/api/routes/workflow.py` 的列表路由、`frontend/src/pages/Workflows.tsx`、`frontend/src/lib/workflow-schedule.ts`。证据来自本次自行构造并实际运行的入口探针，不转述已有测试名称。

工作区基线分支 `codex/flow-003-scheduled-execution`，HEAD `9d4791c96052029d85b002211ed3a4e6de1c89ec`。探针打到的是当前工作区源码，不是该提交里的旧文件。

## 结论

**工程审查通过。** 计划审查要求的关闭不建循环、缺依赖时列表头为 `manual-only` 且手动执行仍可落库、空列表文案只在 `manual-only` 出现，均已在真实入口观察到。无未修复 P1/P2。不得据此把 FLOW-004 标为 done。

## 探针

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0）。文案函数用 Node.js v22.20.0 的 `--experimental-strip-types` 直接加载工作区 `frontend/src/lib/workflow-schedule.ts`。脚本位于 `C:\Users\123\AppData\Local\Temp\flow004-entry-review\`，未写入仓库。

数据库：进程启动前把 `DATABASE_URL` 设为 `sqlite:///C:/Users/123/AppData/Local/Temp/flow004-entry-review/flow004-isolated.db`，并挡住仓库 `.env`。引擎 URL 与该文件一致。探针结束后该库最后写入时间为 2026-10-20 11:07:29；`data/ai_assistant.db` 仍为 2026-10-18 21:54:24，`data/test_ai_assistant.db` 仍为 2026-10-19 14:37:26。两条命令退出码均为 0。

1. `WORKFLOW_ENABLED` 设为关闭后调用 `start_scheduler`。`schedule_mode()` 为 `disabled`，`_task` 为 `None`，事件循环任务数保持 1，没有新建调度循环。带读权限的 `GET /api/v1/workflows` 状态码 503，正文为「工作流引擎未启用（WORKFLOW_ENABLED=false）」，响应里没有 `X-Workflow-Schedule`。
2. 将 `app.workflow.scheduler._require_croniter` 换成抛出 `CroniterNotAvailableError`，开关打开后再调用 `start_scheduler`。调度日志出现「croniter 未安装，调度器不启动」。`schedule_mode()` 为 `manual-only`，`_task` 仍为 `None`，任务数仍为 1。`GET /api/v1/workflows` 状态码 200，响应头 `X-Workflow-Schedule` 为 `manual-only`。随后 `POST /api/v1/workflows/{id}/run` 状态码 201，响应 `triggered_by` 为 `manual`、执行状态 `success`。隔离库中同一条执行记录的 `triggered_by` 也是 `manual`。
3. `workflowScheduleNotice('manual-only')` 返回「未安装 croniter，定时不会自动运行，目前只剩手动执行。」，包含「只剩手动执行」。`scheduled` 与 `disabled` 的返回值都是 `null`，不包含这句。页面空列表把该返回值放在原有「引擎会按周期自动执行」之前；503 分支走「工作流引擎未启用」，不到空列表。

## 本次实际执行的源码指纹

| 文件 | SHA256 |
|---|---|
| `app/workflow/scheduler.py` | `3e18a42aa853513072b6a909a7a45b26b19edb686fc6923eeaa626b25df9d053` |
| `app/api/routes/workflow.py` | `dec7692ee4b2aa4f3e519fbf9829ca7d9d73580e4937edab9ac9374c8c2b014f` |
| `frontend/src/pages/Workflows.tsx` | `44c080bf3649ad829bb0457bbd72bec2ab13fe08857f2c4861e2de5c0f28217c` |
| `frontend/src/lib/workflow-schedule.ts` | `68bb0606c03218991e248ba5da56a07b92af3e0a5088bb49b974b07a247c72b0` |
| `frontend/src/api/workflows.ts` | `42a26567571192bab38e4c9410bfa7258f89a9a1c72050f153ac6200a6fd359a` |

上述指纹变化后，本结论失效。全量 pytest、ruff、mypy、前端四项检查、最终版本绑定、人工审核、业务接受和最终 CI 不在本次审查范围内。
