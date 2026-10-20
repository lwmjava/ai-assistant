# PWFL-001 独立入口审查

日期：2026-10-20。审查身份：未参与 PWFL-001 的实现或计划撰写，只新建本文件。未改产品代码、测试或 `tasks.yaml`，未提交、未推送。不构成任务关闭、业务接受或可发布结论。

已读 `tasks.yaml` 的 PWFL-001、`docs/plans/plan_pwfl_001_20261020.md`，并核对 `app/workflow/engine.py` 的 `execute`、`workflow_retry_limit` 与 `workflow_retry_delay`。证据来自本次实际运行的隔离库 pytest，以及从 `WorkflowEngine.execute` 自行构造的反例，不转述测试名称。

工作区基线分支 `codex/flow-003-scheduled-execution`，HEAD `102c0d18d574e5bfdcf3f58096cb5b51b14a167f`。探针打到的是当前工作区源码，不是该提交里的旧文件。当前工作区里 `app/workflow/engine.py`、`app/core/config.py`、`.env.example` 已修改，计划和重试测试尚未纳入该提交。`app/workflow/scheduler.py` 不在本次变更里，源码中也没有重试接线。

## 结论

**工程审查通过。** 上限为 1 时，第一次 bridge 失败、第二次成功，另开的 Session 只读到一条 `success`，等待值等于 `min(基数 × 2^(n-1), 单次上限, 30)` 的第 1 项。无未修复 P1/P2。本结论不关闭 PWFL-001，不得标为 done。

## pytest

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0，conda-forge）。

```powershell
$env:DATABASE_URL='sqlite:///./data/pwfl001-engineering.db'
$env:PYTHONUTF8='1'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_workflow_retry.py -q --basetemp=data/pytest-tmp/pwfl001-engineering
```

退出码 0。输出末行为 `5 passed in 2.33s`。库文件 `data/pwfl001-engineering.db` 的最后写入时间为 2026-10-20 12:45:18。该次运行之后，`data/ai_assistant.db` 仍为 2026-10-20 11:19:05，`data/test_ai_assistant.db` 仍为 2026-10-19 14:37:26。

## 探针

脚本位于 `C:\Users\123\AppData\Local\Temp\pwfl001-engineering-review\probe.py`，未写入仓库。进程启动前把 `DATABASE_URL` 设为 `sqlite:///C:/Users/123/AppData/Local/Temp/pwfl001-engineering-review/probe.db`。引擎 URL 与该文件一致。`init_db(auto_migrate=False)` 只在该库建表。

配置直接写到进程内 settings：额外重试上限 1，退避基数 5 秒，单次等待上限 8 秒。独立公式取第 1 次重试前的等待 `min(5 × 2^0, 8, 30) = 5.0`。基数 5 且上限 8，使结果既不是单次上限 8，也不是把指数写成 `2^n` 时的 `min(10, 8, 30) = 8`。替身 bridge 第一次抛出 `RuntimeError`，第二次返回固定文本。等待函数只记录秒数，不真正睡眠。`execute` 使用一个 Session；读结果使用另一个新 Session。

该命令退出码 0。可观察结果：

| 观察项 | 结果 |
|---|---|
| `workflow_retry_limit()` | 1 |
| `workflow_retry_delay(1)` | 5.0，与独立公式相同 |
| bridge 调用次数 | 2 |
| 记录到的等待 | `[5.0]` |
| 新 Session 中的执行记录 | 1 条，状态 `success` |
| 该记录的 `error` | `None` |
| 该记录的 `output` | `probe-second-success` |
| `tenant_id` / `triggered_by` | `pwfl001-probe-tenant` / `manual` |
| 新 Session 读到的 id | 与 `execute` 返回的 id 相同：`f3bae6800ac146b0a22ae88c07a5bdd5` |

执行过程中日志打出「Workflow pwfl001-probe-job 执行失败」以及第一次 bridge 异常的栈。随后同一条记录在新 Session 中为 `success`，`error` 为空。这是每次 bridge 异常都走 `logger.exception` 的结果；计划要求最终成功时清空错误，没有要求中间失败保持静默。

探针结束后，`data/ai_assistant.db`、`data/test_ai_assistant.db` 和 `data/pwfl001-engineering.db` 的最后写入时间均未变化。

## 与计划的实现核对

`workflow_retry_limit` 把非整数收成 0，并把整数夹在 0 到 3。`workflow_retry_delay` 把非有限数和负数收成 0，基数与单次等待都不超过 30 秒，第 n 次重试前等待 `min(base × 2^(n-1), max_delay, 30)`。`execute` 只创建一条执行记录，每次调用 bridge 前重新读取创建者和任务；创建者失效时停用任务并返回，不再调用 bridge。`CancelledError` 不落在 `except Exception` 里。只有最终成功才发 webhook。这些与修订后的计划一致，本次没有发现未修复的 P1 或 P2。

## 本次实际执行的源码指纹

| 文件 | SHA256 |
|---|---|
| `app/workflow/engine.py` | `2a548f3ffaa68cb7949f5f4319690889562ca4f609e99f04c20d2d1dc5b7eca8` |
| `app/core/config.py` | `f3103a0c60907b80df77975995be3a4cb6e68c1a2953adf5c5b9c1b739705c7e` |
| `.env.example` | `85790fad609b10eacbb6e3040e6090860be353e95c3185125e55bfa07c135655` |
| `tests/test_workflow_retry.py` | `5a33e6991925206d04a5ab01962d9a83214c2b7759985d78dfcd394811a3485f` |
| `docs/plans/plan_pwfl_001_20261020.md` | `3e5a7c7721cd22f02b82ba82e36edb07fb748f95ec58ff1399f327833477b5d2` |

上述指纹变化后，本结论失效。全量 pytest、ruff、mypy、最终版本绑定、人工审核、业务接受和最终 CI 不在本次审查范围内。
