# 实现说明：调度在目标环境可启动和停止

> 日期：2026-09-30
> 计划：`docs/plans/plan_d1.md` 的 FLOW-001 节
> 结果：调度任务能创建、能取消。工作流调度仍是 `Partial`。到点不会执行。

## 完成的行为

装上 `croniter` 且 `WORKFLOW_ENABLED=true` 时，`start_scheduler()` 创建后台任务，`stop_scheduler()` 能把它停掉。默认开关仍是关闭。缺包时调度器仍不启动，手动触发路径没有改。

1. `requirements.txt` 列入 `croniter>=2.0.0`，与 `pyproject.toml` 的 `workflow` extra 同一版本范围。上一行说明改为：默认安装清单包含该依赖，开关仍默认关闭。
2. README「快速开始」写明 `pip install -r requirements.txt` 和 `pip install -e ".[workflow]"` 会装上 `croniter`，打开开关后只保证创建调度任务。到点执行不写成已完成。「开发」一节写明只装 `.[dev]` 不会带上这个包。
3. `test_scheduler_runnable_and_start_stop` 用 `monkeypatch` 打开开关，结束时回到用例开始前的值。`finally` 里调用 `stop_scheduler()`。没有改成跳过。
4. 能力矩阵第 2 节工作流调度保持 `Partial`。原因改为到点触发没有执行记录。第 7 节 2026-09-13 的全量数字未改，补了一句此后启停用例在装有该包的环境通过。

## 代码位置

- 安装清单：`requirements.txt`
- 安装说明：`README.md` 的「快速开始」和「开发」
- 启停用例：`tests/test_workflow.py` 的 `test_scheduler_runnable_and_start_stop`
- 能力状态：`docs/product/as-is-capability-matrix.md` 第 2 节工作流调度行
- 调度循环未改：`app/workflow/scheduler.py` 的 `_tick()`

## 验证

文档里的解释器路径 `D:\install\anaconda3\envs\ai-assistant\Scripts` 在本机不存在。实际使用的是 conda 环境 `ai-assistant`：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0）。没有改用 base。

`python -m pip install "croniter>=2.0.0"`：该环境里已有 `croniter` 6.2.4，退出码 0。没有重装整份 `requirements.txt`。

```text
python -c "import croniter; print('ok')"
ok
exit 0
```

```text
python -m pytest tests/test_workflow.py -v --tb=short
9 passed in 2.74s
exit 0
```

其中 `test_scheduler_runnable_and_start_stop` 为通过。

`ruff check tests/test_workflow.py` 退出码 1，报的是原有的导入排序 `I001`，这次没有改导入，也没有为了退出码去整理。

## 没做的事

没有构建镜像，因此没有证明镜像里已经装上 `croniter`。没有把 `WORKFLOW_ENABLED` 默认改成开启。没有改 `_tick()`，没有用当前时刻或 `after=last` 去补到点触发，也没有新增那张任务卡。没有把工作流调度标成 `Implemented`。没有改 QA-005 的另外 7 个失败，也没有把 QA-005 标成完成。启停用例没有改成跳过；以后做 QA-005 时不要把它改回 skip。没有改 `pyproject.toml` 的 extra。
