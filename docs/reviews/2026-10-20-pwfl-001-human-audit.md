# PWFL-001 独立人工审核

日期：2026-10-20。审核身份：未参与 PWFL-001 的实现、计划审查或工程审查。本轮只新建本文件。未改产品代码、测试或 `tasks.yaml`，未提交、未推送。用户没有委托业务接受。本结论只说明本次专项测试观察到的行为，任务保持未关闭。

已读 `tasks.yaml` 的 PWFL-001 原始验收、`docs/plans/plan_pwfl_001_20261020.md`，并核对 `app/workflow/engine.py` 的重试循环。证据来自本次实际运行的 pytest，以及对本轮所跑用例和对应源码的核对。计划审查、工程审查和 `tasks.yaml` 里已有的进度叙述不作为本轮证据。

工作区 HEAD 为 `102c0d18d574e5bfdcf3f58096cb5b51b14a167f`。`app/workflow/engine.py`、`app/core/config.py`、`.env.example`、`tasks.yaml` 相对该提交已有未提交修改，`tests/test_workflow_retry.py` 仍未跟踪。pytest 打到的是当前工作区源码。

## 结论

**四项核对成立。** 一次临时失败后，同一条执行记录在第二次调用成功；持续失败在达到上限后停止，并记下上限和最后一次异常文本；额外重试上限为 0 时只调用一次；创建者在进入 bridge 前已禁用时，不调用 bridge，并把任务标为停用。

原始验收两条都落在本次通过的用例上。计划里追加的「上限为 0 只调用一次」和「创建者失效不调用 bridge」也在本次命令中通过。任务不能写成 done。全量回归、真实对话桥接、业务接受和最终 CI 都未在本轮完成。

## 核对

| 核对项 | 本轮观察 |
|---|---|
| 一次临时失败后成功 | `test_transient_failure_retries_and_records_backoff` 通过。`WORKFLOW_RETRY_MAX=1`，基数 2 秒。`CountingBridge` 第一次抛出 `temporary`，第二次返回 `recovered`。调用次数为 2，注入的等待记录为 `[2.0]`，执行记录只有 1 条，`status=success`，`error` 为空，`output=recovered`。等待值符合 `base * 2^(n-1)` 的第一次重试。 |
| 持续失败达到上限后停止并记录原因 | `test_persistent_failure_stops_at_limit` 通过。上限为 2，bridge 被要求连续失败 9 次，实际调用 3 次后停止。等待记录为 `[1.0, 2.0]`。执行记录 1 条，`status=failed`，`error` 为 `已达到重试上限 2：still down`。 |
| 上限 0 只调用一次 | `test_zero_retry_calls_bridge_once` 通过。自动夹具把 `WORKFLOW_RETRY_MAX` 设为 0，与 `app/core/config.py` 的默认值一致。失败 bridge 的调用次数为 1，等待次数为 0，`status=failed`，错误文本包含 `已达到重试上限 0`。源码把最后一次异常文本接在该前缀之后。 |
| 创建者失效不调用 bridge | `test_inactive_owner_does_not_call_bridge` 通过。创建者一开始 `is_active=False`，额外重试上限为夹具中的 0。`bridge.calls` 为 0，`status=failed`，错误文本包含 `创建者已失效`，任务 `enabled=False` 且 `suspended_owner=True`。循环在 `session.get` 到创建者之后、调用 `self._bridge.execute` 之前返回。 |

同一命令还通过 `test_retry_bounds_reject_extremes`：额外重试 `-1` 收成 0、`99` 收成 3；基数为 NaN 且单次上限为 Infinity 时等待为 0；基数 30 且配置上限 100 时，第 1 次和第 3 次重试前的等待都是 30 秒。这是计划里的数值边界，不属于上面四项的原始验收原文。

重试发生在 `WorkflowEngine.execute` 的同一个 `for` 循环里。尝试次数是 `workflow_retry_limit() + 1`。`index == 0` 不暂停；之后先按 `workflow_retry_delay(index)` 等待，再调用 bridge。`CancelledError` 继承 `BaseException`，不进入 `except Exception`。最终成功才把 `error` 清空；循环耗尽时写入 `已达到重试上限 {上限}：{最后一次异常}`。成功后的 webhook 只在 `status == success` 时触发。

## 本次 pytest

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0）。`DATABASE_URL=sqlite:///./data/pwfl001-human-audit.db`，`PYTHONUTF8=1`。

```text
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_workflow_retry.py -q --basetemp=data/pytest-tmp/pwfl001-human-audit
.....                                                                    [100%]
5 passed in 1.91s
exit code: 0
```

输出里没有失败，也没有警告。

隔离：命令前 `data/pwfl001-human-audit.db` 不存在；命令后为 589824 字节，2026-10-20 12:58:31。`data/ai_assistant.db` 仍为 29409280 字节、2026-10-20 11:19:05。`data/test_ai_assistant.db` 仍为 1204224 字节、2026-10-19 14:37:26。`tests/conftest.py` 对 `DATABASE_URL` 使用 `setdefault`，本次进程在启动前已设置审核库地址，因此没有退回默认测试库。

## 源码指纹

| 文件 | SHA256 |
|---|---|
| `app/workflow/engine.py` | `2a548f3ffaa68cb7949f5f4319690889562ca4f609e99f04c20d2d1dc5b7eca8` |
| `tests/test_workflow_retry.py` | `5a33e6991925206d04a5ab01962d9a83214c2b7759985d78dfcd394811a3485f` |
| `app/core/config.py` | `f3103a0c60907b80df77975995be3a4cb6e68c1a2953adf5c5b9c1b739705c7e` |
| `.env.example` | `85790fad609b10eacbb6e3040e6090860be353e95c3185125e55bfa07c135655` |

上述指纹变化后，本结论失效。

## 未验证项

- 本轮 bridge 是测试里的 `CountingBridge`。没有调用真实 `WorkflowBridge` 或 `ChatService`，也没有核对失败尝试留下的对话消息和配额。
- 等待使用测试注入的 `sleeper`。没有走默认的 `asyncio.sleep`。
- 创建者失效用例只覆盖进入执行时已经禁用、且额外重试上限为 0。没有另跑上限大于 0、重试中途禁用、创建者移出租户，或任务行已被删除。
- 没有跑调度器。同一次 `execute` 只写一条记录已在专项里看到；到点触发是否会因为这条记录再跑同一槽，本轮没有执行。
- 最终成功才发 webhook，本轮没有用例覆盖。
- 本轮未重跑无筛选 pytest、`ruff check .`、`mypy app/`，也没有前端检查。计划写明不改前端。
- 没有 Linux 平台结果，也没有最终提交上的 Backend、Frontend、Agent official baseline。
- 业务接受未委托。未提交。PWFL-001 不能写成 done。
