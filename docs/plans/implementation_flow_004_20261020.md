# 工作流开关与缺失依赖提示实现说明

日期：2026-10-20。基线为 FLOW-003 关闭提交 `9d4791c96052029d85b002211ed3a4e6de1c89ec` 加上本卡未提交改动。用户要求关闭上一张卡并用单卡交付技能实现本卡。本说明不是整卡完成结论。

## 实际行为

`WORKFLOW_ENABLED` 仍默认关闭。关闭时 `start_scheduler` 不创建循环，工作流接口仍返回 503，页面仍显示「工作流引擎未启用」。此时手动执行同样不可用，所以页面不写「只剩手动执行」。

开关打开且当前解释器能导入 croniter 时，调度器按原循环启动。到点执行仍由已有 `start_scheduler` 集成测试证明，本卡没有改最近到期判定。

开关打开但 croniter 不可用时，调度器不启动、不让进程崩溃。有读权限的列表响应带 `X-Workflow-Schedule: manual-only`。页面在空列表和已有任务列表都显示：「未安装 croniter，定时不会自动运行，目前只剩手动执行。」手动执行仍写入 `triggered_by=manual` 的记录。未登录或无读权限的列表请求不会带出这个响应头。

## 代码位置

- `app/workflow/scheduler.py`：`schedule_mode`
- `app/api/routes/workflow.py`：列表响应头
- `frontend/src/lib/http.ts`：`getResponse`
- `frontend/src/api/workflows.ts`、`frontend/src/pages/Workflows.tsx`、`frontend/src/lib/workflow-schedule.ts`

## 验证

指定解释器下，隔离库 `sqlite:///./data/flow004-full.db`：无筛选 pytest **3086 passed、3 skipped、3 既有警告、728.32 秒、退出码 0**。其中关闭不建循环、依赖齐全时响应头为 `scheduled`、无读权限看不到响应头、缺 croniter 时响应头为 `manual-only` 且手动执行成功。已有到点循环测试仍通过。

`ruff check . --no-cache` 与 `mypy app/` 退出码 0。前端类型检查、Vitest 167 项、ESLint 和生产构建退出码 0。

## 没做的事

没有重试、并发池、告警或新的到点算法。没有新增路由、配置、依赖或迁移。没有在浏览器里登录后点开工作流页，页面文案目前由纯函数测试和接口响应头证明。

独立工程审查见 `docs/reviews/2026-10-20-flow-004-engineering-review.md`，人工审核见 `docs/reviews/2026-10-20-flow-004-human-audit.md`。人工审核重跑 4 条入口测试，4 passed、2.89 秒、退出码 0，无未修复 P1/P2。独立页面验收见 `docs/reviews/2026-10-20-flow-004-page-acceptance.md`。隔离库上实际打开工作流页：关闭时可见「工作流引擎未启用」且列表为 503；拦截 croniter 时可见「目前只剩手动执行」且响应头为 `manual-only`；依赖齐全时不出现该句且响应头为 `scheduled`。业务接受和本提交的三项 CI 尚未返回，卡保持 `in_review`，不能标 done。
