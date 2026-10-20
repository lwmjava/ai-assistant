# 工作流失败重试实现说明

日期：2026-10-20。基线 `102c0d18d574e5bfdcf3f58096cb5b51b14a167f`。用户要求关闭上一张卡并用单卡交付技能实现本卡。本说明不是整卡完成结论。

## 实际行为

`WORKFLOW_RETRY_MAX` 默认 0。为 0 时，bridge 失败只尝试一次，执行记录写入失败原因。设为 1 到 3 时，同一次 `WorkflowEngine.execute` 在失败后按指数退避再次调用 bridge。次数小于 0 按 0，大于 3 按 3。等待基数和单次等待小于 0 或不是有限数时按 0，大于 30 秒时按 30 秒。

成功或最终失败都只留下一条执行记录。成功时状态为 success，错误为空。最终失败时错误文本包含最后一次异常和已达到的重试上限。每次调用 bridge 前重新读取创建者；创建者失效则停用任务并不再调用 bridge。只有最终成功才发 webhook。调度器的到期判定没有改。

失败尝试若已进入对话服务，可能留下消息并计入配额。本卡没有改对话服务，也没有做跨尝试抵消。

## 代码位置

- `app/workflow/engine.py`：`workflow_retry_limit`、`workflow_retry_delay` 和 `execute`
- `app/core/config.py`、`.env.example`
- `tests/test_workflow_retry.py`

## 验证

隔离库上 `tests/test_workflow_retry.py` 与 `tests/test_workflow.py` 共 18 passed、2.77 秒、退出码 0。随后无筛选 pytest **3091 passed、3 skipped、3 既有警告、732.87 秒、退出码 0**。`ruff check . --no-cache` 与 `mypy app/` 218 个文件退出码 0。

独立工程审查见 `docs/reviews/2026-10-20-pwfl-001-engineering-review.md`，人工审核见 `docs/reviews/2026-10-20-pwfl-001-human-audit.md`。人工审核重跑专项 5 passed、1.91 秒、退出码 0。没有未修复的 P1/P2。用户于 2026-10-20 委托另一名未参与实现与既有审查的 Agent 做人工业务验收，结论见 `docs/reviews/2026-10-20-pwfl-001-business-acceptance.md`。专项 5 passed、2.55 秒、退出码 0。探针在新 Session 读到：临时失败后 2 次调用、等待 `[2.0]`、一条 success；持续失败 3 次调用后停止，错误为「已达到重试上限 2：still down」。业务验收通过。实现尚未提交，最终 SHA 的三项 CI 未验证，卡保持 `in_review`，不能标 done。

## 没做的事

没有分布式重试、超时、取消、并发池、告警或 cron 比较改动。没有提交或部署。

## 关闭补记（2026-10-20）

用户明确确认 GitHub CI 已通过并接受关闭，PWFL-001 更新为 done。最终实现提交为 `8e81bb289b9ed10b175041faf212f7c916074b9f`；原文待提交/待CI是历史状态，由本补记覆盖。独立复核见 `docs/reviews/2026-10-20-pwfl-001-closure-recheck.md`：专项5 passed、2.01秒、exit0，4020项冻结输入变化0，原关键重试指纹一致，无未决P1/P2。CI按用户提供证据登记，不伪称本轮独立获得run URL或核验分支保护。主Agent另跑当前workflow基线59 passed、18.57秒、exit0，Ruff无缓存与mypy219文件exit0。没有修改业务、测试或配置；仅同步关闭证据，不重新启用生产或执行模型。
