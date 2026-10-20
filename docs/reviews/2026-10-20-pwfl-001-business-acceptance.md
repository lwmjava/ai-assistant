# PWFL-001 人工业务验收

日期：2026-10-20。

## 身份与授权

身份：PWFL-001 的独立人工业务验收。本轮没有参与本卡实现、计划审查、工程审查或先前的人工技术审核。

授权出处：用户于 2026-10-20 明确委托“启用一个 Agent 对该任务做人工业务验收”。

本轮只新建本文件和同目录的 pytest 输出记录。未改产品代码、测试或 `tasks.yaml`，未提交、未推送。

工作区 HEAD 为 `102c0d18d574e5bfdcf3f58096cb5b51b14a167f`。相对该提交，`app/workflow/engine.py`、`app/core/config.py`、`.env.example`、`tasks.yaml` 已有未提交修改，`tests/test_workflow_retry.py` 仍未跟踪。本轮 pytest 和探针打到的都是当前工作区源码。

## 原始验收

`tasks.yaml` 中 PWFL-001（约 5104 行）的验收只有两条：

- 注入一次临时失败后可自动重试成功
- 持续失败达到上限后停止并记录原因

交付是失败按指数退避重试并设上限，且重试次数与退避参数可配置。非目标是不做分布式重试协调、不改 cron 触发机制。

产品 `docs/product/项目产品需求方案.md` §5.6.2「失败重试」：`retry_count` 默认 0、最大 3；misfire 策略为只补跑最近一次。

`docs/plans/plan_pwfl_001_20261020.md` 第 3 节已批准行为：重试发生在同一次 `WorkflowEngine.execute` 内，同一条执行记录从 pending 到结束，不新增多条记录。`WORKFLOW_RETRY_MAX` 是额外重试次数，默认 0，小于 0 按 0，大于 3 按 3。默认 0 时失败只尝试一次。第 n 次重试前等待 `min(base * 2^(n-1), max_delay, 30)` 秒。最终失败的 `error` 保留最后一次异常文本，并写明已达到的重试上限。成功时 `status=success`，`error` 为空。

`app/workflow/engine.py` 的 `execute` 与上述行为一致：尝试次数为 `workflow_retry_limit() + 1`；第一次不暂停，其后调用注入的等待函数再调用 bridge；循环耗尽时写入 `已达到重试上限 {上限}：{最后一次异常}`。

默认额外重试次数为 0 时一次失败就停止，是已批准的默认行为，不记为验收失败。

## 结论

**业务验收通过。** 两件可观察事实都成立：配置允许至少 1 次额外重试时，注入一次临时失败后自动重试成功，提交后新 Session 只读到一条 success 记录，并且发生过一次等待；持续失败在达到上限后停止，记录里能读到失败原因和上限，没有无限重试。

这不等于整卡 done。实现尚未提交。最终 SHA 的 Backend、Frontend、Agent official baseline 未验证。本结论不建议把 `tasks.yaml` 改成 done。任务保持 `in_review`。

## pytest

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。工作目录为仓库根目录。隔离库为 `sqlite:///./data/pwfl001-business-acceptance.db`。

```text
$env:DATABASE_URL='sqlite:///./data/pwfl001-business-acceptance.db'
$env:PYTHONUTF8='1'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_workflow_retry.py -q --basetemp=data/pytest-tmp/pwfl001-business-acceptance
```

退出码：0。输出：`5 passed in 2.55s`。原文见 `docs/reviews/2026-10-20-pwfl-001-business-acceptance-pytest.txt`。

该命令覆盖专项测试里的临时失败后成功、持续失败到上限、上限 0 只调用一次，以及数值边界和创建者失效。业务结论以下一节的引擎探针为准，不转述先前审核日志。

## 探针可观察结果

探针是仓库外临时脚本 `C:\Users\123\AppData\Local\Temp\pwfl001-business-acceptance-probe.py`。库为 `sqlite:///C:/Users/123/AppData/Local/Temp/pwfl001-business-acceptance-probe.db`。脚本在导入应用前设置 `DATABASE_URL`，调用 `init_db()`，用合成 bridge 调用 `WorkflowEngine.execute`。等待函数只记录被传入的秒数，不真实长睡。`execute` 返回并提交后，用新的 `Session` 按 `workflow_id` 读取执行记录。探针退出码 0。结束后临时库已删除，复查该路径不存在。

`Settings` 字段 `WORKFLOW_RETRY_MAX` 的代码默认值是 0。

| 场景 | 配置 | 可观察结果 |
|---|---|---|
| 一次临时失败后成功 | 额外重试 1，基数 2 秒；bridge 第一次抛出 `temporary outage`，第二次返回 `recovered` | bridge 调用 2 次。等待记录 `[2.0]`，发生过一次等待。新 Session 只有 1 条记录，`id=b2a418c3b61a4f94b9b71aa804b131a5`，`status=success`，`error` 为空，`output=recovered`，`tenant_id=transient-tenant`，`triggered_by=manual`。返回记录与这条是同一 id。 |
| 持续失败到达上限 | 额外重试 2，基数 1 秒；bridge 被设计为连续失败 50 次，安全帽为 8 次 | 实际调用 3 次后停止，等于 1 加上限，没有打到安全帽。等待记录 `[1.0, 2.0]`。新 Session 只有 1 条记录，`id=5398d6a2d24849b09699ec0bbf580269`，`status=failed`，`error=已达到重试上限 2：still down`。失败原因和上限都能从这条记录读到。 |
| 默认额外重试 0 | 使用字段默认值 0；bridge 会持续失败 | 调用 1 次，等待记录为空，随后停止。新 Session 只有 1 条记录，`status=failed`，`error=已达到重试上限 0：first failure`。这是默认行为，不构成验收失败。 |

## 未验证项

- 当前改动没有提交。HEAD `102c0d18d574e5bfdcf3f58096cb5b51b14a167f` 不是本卡实现的最终 SHA。
- 最终提交的 Backend、Frontend、Agent official baseline 没有核对，也没有对应 CI run。
- 本轮没有重跑无筛选 pytest、ruff 或 mypy。
- 探针使用合成 bridge 和注入的等待函数。没有走真实 `WorkflowBridge` / `ChatService`，也没有用默认 `asyncio.sleep` 做墙钟等待。
- 创建者失效、cron 触发、webhook、分布式重试和 misfire 不在本轮两件业务事实的判定范围内。
- pytest 使用的 `data/pwfl001-business-acceptance.db` 留在已忽略的 `data/` 下。探针临时库已删除。
