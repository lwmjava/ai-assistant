# FLOW-003 最终独立人工审核

日期：2026-10-20。

## 身份说明

本审核 Agent 未参与 FLOW-003 的实现、计划审查或工程审查，也未修改产品代码、测试或 `tasks.yaml`。本轮只读取原始契约、已落盘计划、实现说明、验收对账、工程审查和调度入口，重算指纹，实际重跑指定测试，并自行运行隔离探针。依据是 `docs/ai-prompts/skills/task-card-delivery/SKILL.md` 的 Workflow 步骤 10 与关闭判定：自动验收之后仍须独立人工审核；用户没有委托代行业务接受时，不得 done。

仓库 `E:/culture/SmartCustomerServiceSystem/ai-assistant`，分支 `codex/flow-003-scheduled-execution`，基线 HEAD `150afa8a8df771f60beb0fede8fe7038c40c500a`。本轮 `git rev-parse HEAD` 与该基线一致。解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0），croniter 6.2.4。

原始契约：到点进入执行并写入 `WorkflowExecution`；只调用手动执行不算通过；本卡不做重试。

## 指纹核对

对照 `docs/reviews/2026-10-20-flow-003-evidence.json` 的 `input_sha256`，以及工程审查报告中的冻结输入表。五个文件在测试前、测试后两次重算，结果相同，与两份既有指纹一致。旧证据不因这五个输入失效。

| 文件 | SHA256 |
|---|---|
| `app/workflow/engine.py` | `0b11a570d62755541c8044ab1bd333c4d25d7dfdfc800b20c810ecee27697b1c` |
| `app/workflow/scheduler.py` | `3cbfd480dc090139fac83c2d233a7a15421d460bc718a4520de78801e3133f41` |
| `tests/test_workflow.py` | `cb08ceb68615a52de27bf3c7dfe38aea2071fd16a040c34b3f39a46f77f472c2` |
| `tests/test_workflow_schedule_contract.py` | `2ada07778ddb875b61a20e42636607d746989861f1c392a32de11405f4812f05` |
| `tests/test_workflow_schedule_integration.py` | `9d38804854a7c0a669a0a527678b4d177bb1e71840171c8b1b02899764c6357d` |

工作区里 `engine.py`、`scheduler.py` 相对基线已修改，两份调度测试仍未跟踪。指纹核对的是当前工作区字节，不是基线提交内的旧副本。

## 实际命令与退出码

```powershell
$env:DATABASE_URL='sqlite:///./data/flow003-human-audit-test.db'
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_workflow.py tests/test_workflow_schedule_contract.py tests/test_workflow_schedule_integration.py -q --basetemp=data/pytest-tmp/flow003-human-audit
```

退出码 **0**。结果 **48 passed，1 warning，17.02 秒**。警告是既有 `sandbox.py` 的 `ast.NameConstant` 弃用提示。完整输出在 `docs/reviews/2026-10-20-flow-003-human-audit-pytest.txt`。

PowerShell 的 `*>` 首次落盘为 UTF-16 LE（1146 字节，SHA256 `c5abff74dc590da1394902c7a16e608b68ec28a668fb2290c208bd4cc1145544`）。审核文件保留的是同一段文本的 UTF-8 转写（576 字节）。UTF-8 日志 SHA256：

`9fe8b35646219c7f3a03ffa86d45b321ec731767ae9ef4e41f96158e1d351305`

这是本轮新跑的结果，不是转述工程审查的 12.84 秒日志。本轮没有重跑全量 pytest、Ruff 或 mypy。

## 尝试过的反例及结果

正式测试已经覆盖“桥接失败后同一时刻再 tick 不新增记录”，以及直接调用 `_trigger` 时禁用、删除、暂停旧快照。工程审查实际跑过的是计划身份变化、租户失效、`created_at` 被改写和租户恢复。本轮仍从真实 `_tick` 入口另跑探针，脚本只放在本机临时目录，隔离库为 `data/flow003-human-audit-probe.db`，跑完已删除。探针使用真实 `_tick` → `_trigger` → `WorkflowEngine`，合成 Bridge，提交后用新 Session 读取 `WorkflowExecution`。探针退出码 **0**。失败用例的 `logger.exception` 打印了合成 `RuntimeError` 栈，这是执行器对桥接失败的既有日志，不是探针失败。

1. 到点执行失败后不重试该 slot。`* * * * *`，时钟停在 UTC 12:00，Bridge 对提示词 `fail` 抛错。第一次 tick 写入 1 条 `triggered_by=cron`、`status=failed`、`error=synthetic bridge failure` 的记录，Bridge 调用 1 次。同一时钟再 tick，仍是 1 条、1 次调用。清空进程水位后再 tick，仍是 1 条、1 次调用，说明失败记录的 `created_at` 已经挡住同 slot。时钟推进 1 分钟后出现第 2 条失败记录和第 2 次调用；这是下一分钟的新尝试，不是把 12:00 重跑一遍。
2. 亚秒扫描。时钟为 UTC 12:00:00.400000，同样失败一次。再 tick 仍是 1 条记录、1 次调用。落库 `created_at` 为 `2026-10-20 12:00:00.400000`。
3. 扫描后禁用。在 `_most_recent_execution_time` 里把已扫描任务 `enabled` 改为 false，同时保留另一个仍启用的任务。目标任务 0 条记录、0 次调用；同伴任务写入 1 条 cron success。清空进程水位后再 tick，目标任务仍然没有记录。
4. 扫描后暂停。同一窗口把 `suspended_owner` 改为 true。目标任务 0 条记录；同伴任务 1 条 cron success。清空水位后目标任务仍然不执行。

四条都符合原始契约和已批准计划：失败消费该 slot，禁用或暂停的旧快照不进入执行。没有发现未修复的 P1 或 P2。

## 关闭对账抽查

对照原始卡、`docs/plans/plan_flow_003_20261020.md`、`docs/plans/implementation_flow_003_20261020.md` 和 `docs/reviews/2026-10-20-flow-003-acceptance.md`。

| 原始项 | 证据类别 | 本轮核对 |
|---|---|---|
| 到点进入执行并写入执行记录 | E2：真实调度入口、原执行器、隔离 SQLite；集成测试含已启动循环，模型为合成 | 本轮 48 项通过，且探针自行看到 cron 记录。通过 |
| 只调用手动执行不能算通过 | E2：`test_running_loop_crosses_due_time_without_manual_trigger` 在上述 48 项内 | 本轮没有用手动执行代替验收。通过 |
| 不做重试 | 非目标。失败记录参与水位，同 slot 不再进入 Bridge | 探针实测同 slot 不重试，下一分钟才是新尝试。符合非目标 |
| 用户业务接受 | 未验证 | 用户没有委托代行业务接受 |
| 最终提交的 Backend、Frontend、Agent official baseline | 未验证 | 没有提交或推送授权，不能用未提交工作区代替最终 SHA 的三项 CI |

验收对账把必交付项标成 E2，并把业务接受和三项 CI 标成未验证。抽查后这条对账成立。全量 3082 项、Ruff 和 mypy 仍只属于先前证据；本轮指纹未变，所以没有把它们判为失效，也没有把它们当作本轮新跑结果。

`tasks.yaml` 里 FLOW-003 当前仍是 `in_progress`。本审核按授权没有改卡面。

## 结论

技术审核通过，卡保持 in_review，等待用户业务接受与最终提交 CI。

没有未修复的 P1 或 P2。不得标 done，本轮也没有提交、推送或部署。
