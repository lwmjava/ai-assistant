# FLOW-003 业务验收

日期：2026-10-20。结论：**业务验收通过**。这不等于本卡 `done`。

## 身份

本文件由独立业务验收 Agent 在 2026-10-20 当场阅读条款并运行命令后写成。该 Agent 没有参与 FLOW-003 的实现、计划审查、工程审查，也没有参与此前的人工技术审核。本次没有修改产品代码、测试或 `tasks.yaml`，没有提交、推送、合并或部署。

## 授权出处

用户于 2026-10-20 明确委托启用一个 agent 做 FLOW-003 业务验收。仓库 `E:/culture/SmartCustomerServiceSystem/ai-assistant`，分支 `codex/flow-003-scheduled-execution`。验收时 `git rev-parse HEAD` 为 `150afa8a8df771f60beb0fede8fe7038c40c500a`，与给定基线一致。`app/workflow/engine.py`、`app/workflow/scheduler.py` 等实现仍是未提交工作区改动。本次判断的是这次实际运行到的工作区，不是一个新的提交 SHA。

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。

## 原始验收条款

`tasks.yaml` 中 FLOW-003（约 7558 行）原始要求：

- 交付：到点时进入执行并写入执行记录。
- 非目标：不用手动执行代替本条；不在本条做重试，重试是 PWFL-001。
- 验收：设定的下一次时间到达后有执行记录；只调用手动执行路径不能算通过。

`docs/plans/plan_v1_lock_20261002.md` 中 FLOW-003 那一行：`next_run_time` 与当前时间的比较能在到点时进入执行，并写入 `WorkflowExecution`。手动执行已经会写记录，不能用手动路径代替这一条。

`docs/product/项目产品需求方案.md` 与本卡范围有关的句子：

- WFL-02：用户能查看任务执行历史，包含每次执行的时间、输入、输出、状态。
- §5.6.2 执行历史：每次执行记录时间、状态、输出、耗时。
- §5.6.2 执行身份：以任务创建者身份执行，注入其 `user_id` 与 `tenant_id`。
- §5.6.2 失败重试与 misfire：只补跑最近一次（`run_latest`）。重试次数、告警、Webhook 不在本卡验收内。

`docs/plans/plan_flow_003_20261020.md` 第 4 节已批准行为：调度从 `_loop` / `_tick` 进入；每次扫描对每个任务最多执行最近一次到期时刻；错过多轮不逐条补历史；失败不在本卡循环重试。不承诺跨重启精确 slot、恰好一次、并发池或实时 SLA。

本次只判定三件可观察事实：

1. 设定的下一次时间到达之后，新会话能读到 `triggered_by=cron` 的 `WorkflowExecution`，含租户，以及输入或输出或失败原因，和状态。
2. 证据来自真正调度入口（已启动的 `_loop` 或真实 `_tick`），不调用手动执行 API，也不直接调用 `WorkflowEngine.execute`。
3. 到点之前零记录；跨过该时刻后才出现记录。

## 代码核对（只读）

`app/workflow/scheduler.py` 的 `_tick` 用当前 UTC 时间调用 `latest_due_time`，未到点或已消费则不调用 `_trigger`；到点后 `_trigger` 重新读取工作流与创建者，再以 `triggered_by=cron` 进入 `WorkflowEngine.execute`。`app/workflow/engine.py` 的 `latest_due_time` 返回不晚于扫描时刻的最近 cron 时刻。本次探针没有改这两处。

## 实际命令与退出码

在仓库根目录执行：

```powershell
$env:DATABASE_URL='sqlite:///./data/flow003-business-acceptance-test.db'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_workflow_schedule_integration.py -q --basetemp=data/pytest-tmp/flow003-business-acceptance
```

退出码：**0**。结果：**6 passed, 1 warning in 4.23s**。警告是 `app/agents/tools/sandbox/sandbox.py` 的 `ast.NameConstant` 弃用警告，不是本卡断言失败。完整输出见 `docs/reviews/2026-10-20-flow-003-business-acceptance-pytest.txt`。该命令按指定 `DATABASE_URL` 留下 `data/flow003-business-acceptance-test.db`，并留下 `--basetemp` 目录 `data/pytest-tmp/flow003-business-acceptance`。这组测试实际断言使用的是各自临时库；业务探针库已按要求删除。

这组测试走真实 `_tick` 和已启动的 `_loop`，并用受控时钟跨过设定时刻。它证明集成文件里的调度入口会落 cron 执行记录。到点前后的墙钟证据以下面的探针为准。

## 探针输入与可观察结果

临时脚本放在仓库外的系统临时目录，运行后已删除。脚本只调用 `app.workflow.scheduler._tick`。没有调用手动执行 API，没有直接调用 `WorkflowEngine.execute`。

输入：

- 隔离库：`sqlite:///C:/Users/123/AppData/Local/Temp/flow003-business-acceptance-probe.db`。进程内确认 `settings.DATABASE_URL` 指向该文件。结束后该文件已删除，`database_deleted=true`。
- 合成模型：`MockLLMProvider`，经 `set_llm_provider_override` 注入既有对话链路。`RAG_ENABLED`、`EVOLUTION_ENABLED`、`LLM_RESPONSE_CACHE_ENABLED` 关闭。
- 租户 `flow003-probe-tenant`，创建者 `flow003-probe-owner`，工作流 `44fc11b904b74652ac3701f618bd55e1`。
- cron：`* * * * *`，时区 `Asia/Shanghai`，输入模板 `flow003 business probe prompt`。
- 工作流创建时间：`2026-10-20T02:00:41.812287+00:00`。
- 下一次到期：`2026-10-20T10:01:00+08:00`（即 `2026-10-20T02:01:00+00:00`）。

操作与结果：

| 观察点 | 时钟 | 新 Session 读到的执行记录 |
|---|---|---|
| 第一次真实 `_tick` | `2026-10-20T02:00:41.843391+00:00`，早于到期 | **0** 条 |
| 等待约 18.357 秒后第二次真实 `_tick` | `2026-10-20T02:01:00.204356+00:00`，已经跨过到期 | **1** 条 |

跨过到期后的那一条：

- `id`：`4702e34d42c942afbdade8558b6b4783`
- `tenant_id`：`flow003-probe-tenant`
- `triggered_by`：`cron`
- `status`：`success`
- `input`：`flow003 business probe prompt`
- `output`：以 `[mock] 已收到你的消息` 开头的合成回复
- `error`：空
- `started_at`：`2026-10-20T02:01:00.205441+00:00`
- `finished_at`：`2026-10-20T02:01:00.252753+00:00`
- `duration_ms`：`47`

读取发生在执行提交之后的新 `Session`。探针进程退出码：**0**。导入阶段出现 `fitz` API 弃用警告，没有改变上述计数。

合成模型只证明调度进入执行并写出记录。它不能证明真实回答质量，也不能证明生产可用。

## 结论

三件事实都成立：

1. 下一次时间到达后，新会话读到 cron 来源的执行记录，租户、输入、输出和状态都在。
2. 记录来自真实 `_tick`，不是手动执行 API，也不是直接调用 `WorkflowEngine.execute`。
3. 到点前 0 条，跨过该时刻后 1 条。

**业务验收通过。**

这不等于整卡 `done`。实现尚未形成最终提交 SHA。该 SHA 上的 Backend、Frontend、Agent official baseline 仍未验证。本验收不得被用来把 `tasks.yaml` 改成 `done`。

## 仍未验证

- 最终提交 SHA 的 Backend、Frontend、Agent official baseline。
- 失败重试。本卡非目标，交给 PWFL-001；本次成功路径没有制造失败后再 tick。
- 错过多轮时只补最近一次的墙钟场景。已批准行为如此，本次探针只跨过紧接着的一个分钟槽。
- 并发池、告警、Webhook、运行中开关界面。
- 跨重启后的精确 slot 或恰好一次。计划已写明不承诺，本次没有重启进程再对同一槽核对。
- 真实大模型的回答质量，以及生产环境启用调度。
- Linux 平台与远程 CI。
