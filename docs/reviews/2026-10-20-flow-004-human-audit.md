# FLOW-004 独立人工审核

日期：2026-10-20。审核身份：未参与 FLOW-004 的实现、计划审查或工程审查。本轮只新建本文件和 `docs/reviews/2026-10-20-flow-004-human-audit-pytest.txt`。未改产品代码、测试或 `tasks.yaml`，未提交、未推送。用户没有委托代行业务接受。本结论不是业务接受，也不得把 FLOW-004 标为 done。

已读 `tasks.yaml` 的 FLOW-004、`docs/plans/plan_flow_004_20261020.md`、`docs/plans/implementation_flow_004_20261020.md`、`docs/reviews/2026-10-20-flow-004-engineering-review.md`。证据来自本次实际运行的 pytest，以及对本轮所跑用例和对应源码的核对。实现说明里的全量回归数字不作为本轮证据。

工作区基线分支上的 HEAD 为 `9d4791c96052029d85b002211ed3a4e6de1c89ec`。本卡改动仍未提交。pytest 打到的是当前工作区源码。

## 结论

**技术审核通过。** 三条可执行验收在本次指定测试里成立：关闭时 `start_scheduler` 不建循环；打开且当前解释器能导入 croniter 时，真正的 `start_scheduler` 到点写出 `triggered_by=cron` 的记录；模拟缺 croniter 时列表响应头为 `manual-only`，手动执行返回 201 且 `triggered_by=manual`。

不得据此把 FLOW-004 标为 done。页面实操未验证。业务接受、提交和最终 CI 都未发生。

## 三条验收

| 原始验收 | 本轮观察 |
|---|---|
| 关闭时不自动跑定时 | `test_disabled_switch_does_not_start_scheduler` 通过。用例把 `WORKFLOW_ENABLED` 设为关闭，先停再调 `start_scheduler`，断言 `schedule_mode()` 为 `disabled`，且 `_task` 为空或已结束。`start_scheduler` 在不可运行时直接返回，不创建循环。 |
| 打开且依赖齐全时到点可触发 | `test_schedule_header_when_dependency_present` 在开关打开的列表夹具上得到 `X-Workflow-Schedule: scheduled`。`test_running_loop_crosses_due_time_without_manual_trigger` 调用真实 `start_scheduler`，断言后台任务已创建，并在受控时钟跨过到期点后落下一行 `triggered_by=cron`、`status=success` 的执行记录。该夹具打开开关；调度器只有在 `schedule_mode()` 为 `scheduled` 时才创建任务。当前解释器可以导入 croniter。 |
| 缺 croniter 时有只剩手动的信号，且手动执行仍成功 | `test_missing_croniter_keeps_manual_execution` 通过。用例把 `_require_croniter` 换成抛出 `CroniterNotAvailableError`，再调 `start_scheduler`。模式为 `manual-only`，循环未创建。`GET /api/v1/workflows` 为 200，响应头 `X-Workflow-Schedule` 为 `manual-only`。`POST /api/v1/workflows/{id}/run` 为 201，`triggered_by` 为 `manual`。这是卡面要求的模拟缺依赖，不是卸载已安装的包。 |

原始卡还要求页面写明只剩手动执行。源码里 `workflowScheduleNotice('manual-only')` 返回「未安装 croniter，定时不会自动运行，目前只剩手动执行。」，空列表用它替换「引擎会按周期自动执行」，已有任务列表另显示同一句。本轮没有登录环境，没有打开工作流页，页面实操未验证。

## 本次 pytest

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0）。`DATABASE_URL=sqlite:///./data/flow004-human-audit.db`，`PYTHONUTF8=1`。完整输出见 `docs/reviews/2026-10-20-flow-004-human-audit-pytest.txt`。

```text
4 passed, 1 warning in 2.89s
exit code: 0
```

唯一警告是 `app/agents/tools/sandbox/sandbox.py` 里既有的 `ast.NameConstant` 弃用警告，出现在缺依赖用例期间，不是失败。

隔离：`data/flow004-human-audit.db` 于本次命令后出现，589824 字节，2026-10-20 11:09:37。`data/ai_assistant.db` 仍为 29409280 字节、2026-10-18 21:54:24。`data/test_ai_assistant.db` 仍为 1204224 字节、2026-10-19 14:37:26。`data/flow004-full.db` 仍为 974848 字节、2026-10-20 11:03:38。到点用例使用 `--basetemp=data/pytest-tmp/flow004-human-audit` 下的临时库。

## 源码指纹

| 文件 | SHA256 |
|---|---|
| `app/workflow/scheduler.py` | `3e18a42aa853513072b6a909a7a45b26b19edb686fc6923eeaa626b25df9d053` |
| `app/api/routes/workflow.py` | `dec7692ee4b2aa4f3e519fbf9829ca7d9d73580e4937edab9ac9374c8c2b014f` |
| `frontend/src/pages/Workflows.tsx` | `44c080bf3649ad829bb0457bbd72bec2ab13fe08857f2c4861e2de5c0f28217c` |
| `frontend/src/lib/workflow-schedule.ts` | `68bb0606c03218991e248ba5da56a07b92af3e0a5088bb49b974b07a247c72b0` |
| `frontend/src/api/workflows.ts` | `42a26567571192bab38e4c9410bfa7258f89a9a1c72050f153ac6200a6fd359a` |
| `frontend/src/lib/http.ts` | `40e278857c77c850605b650903d00b33885bcc1c8d6f9ddedf28a3b7b2a5c12a` |
| `tests/test_workflow.py` | `cc062eed573617dc43729549e23614e38cfa8c87ed420eca5d85165df541e082` |
| `tests/test_workflow_schedule_integration.py` | `9d38804854a7c0a669a0a527678b4d177bb1e71840171c8b1b02899764c6357d` |

上述指纹变化后，本结论失效。

## 未验证项

- 浏览器页面实操：当前终端没有已登录的工作流页。没有打开页面核对关闭文案和「只剩手动执行」。
- 本轮未重跑无筛选 pytest、`ruff check .`、`mypy app/`、前端类型检查、Vitest、ESLint、构建，以及 Linux 上的 Backend、Frontend、Agent official baseline。
- 业务接受未委托。未提交。任务状态不得写成 done。
