# 实现说明：Supervisor 分派并返回结果

> 日期：2026-10-01
> 计划：`docs/plans/plan_d2.md` 的 SUP-001 节
> 结果：显式打开且已安装 extra 时，一次请求能看到分给 `research` 与 `draft` 的记录，最终回答是撰写结果。默认配置仍走五阶段管线。没装上时对话回退五阶段管线。

## 完成的行为

1. `pyproject.toml` 增加可选 extra `langgraph>=1.0.0,<1.1`。`requirements.txt` 只加注释，写明同一版本范围和安装命令 `pip install -e ".[langgraph]"`。该行没有取消注释。
2. 默认 `AGENT_ORCHESTRATION=self` 时，`_build_pipeline()` 返回五阶段管线。`.env.example` 仍是 `self`。
3. 配置为 `langgraph` 但导入失败时，对话回退五阶段管线。这条用打桩的 `ImportError` 覆盖，不依赖机器上是否装了包。
4. 图执行期间，`research` 和 `draft` 各自复制已有分派列表，追加 `{name, result}`，再把完整列表写回。连续两次调研都会留下。`answer` 仍是最终撰写结果。分派记录只留在这次执行的内存里，不入库。
5. 流式输出仍先把图跑完。在 token 之前按顺序发出 `subtask`。数据是 JSON 字符串，`v` 为 1，`status` 为 `done`。摘要先经 `LogSanitizer`，再截到 500 个字符。没有文本时摘要是「没有文本结果」。内存里的 `result` 仍是完整原文。
6. 图执行抛错时不发 `subtask`，沿用原来的道歉句。异常原文不进入事件。
7. 技能上下文仍然不传给 Supervisor。入口仍用 `set_entry_point("supervisor")`，这次构造图成功，没有改成 `add_edge(START, ...)`。

## 代码位置

- `app/agents/supervisor.py`：分派列表、完成后的摘要事件。
- `app/agents/pipeline.py`：`AgentState.delegations`。
- `pyproject.toml`、`requirements.txt`、`README.md`：可选 extra 与安装说明。
- `tests/test_supervisor.py`：分派、两次调研、脱敏截断、空文本、失败不发摘要、默认路径、导入失败回退。

## 验证

文档里的解释器路径 `D:\install\anaconda3\envs\ai-assistant\Scripts` 在这台机器上不存在。下面用的是同名环境 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。

```text
python -m pip install -e ".[langgraph]"
退出码 0
装上的版本是 langgraph 1.0.10

python -c "from langgraph.graph import END, StateGraph"
退出码 0

pytest tests/ -k supervisor -v
18 passed, 2 skipped, 454 deselected
```

18 条通过里包含分派、两次调研都保留、摘要脱敏且不超过 500 字、空文本用固定短句、图失败不发摘要、默认路径是五阶段管线，以及打桩 `ImportError` 后的回退。两条 skipped 是既有用例：缺少 `mcp`、缺少 `llamaindex`。分派用例没有跳过。

`ruff check` 对 `tests/test_supervisor.py` 和 `app/agents/pipeline.py` 没有新问题。`app/agents/supervisor.py` 仍有 4 条原先就有的告警（`Optional` 写法，以及 `END`、`StateGraph` 这两个导入名）。没有为了退出码去改它们。

## 没做的事

- 没有把默认编排改成 Supervisor，也没有把 `langgraph` 放进默认安装清单。
- 没有重写五阶段管线，没有把技能注入补进 Supervisor。
- 没有改成节点进行中的实时流。`subtask` 是图跑完后的摘要。
- 没有把分派记录入库，没有新建 CI，没有构建镜像。镜像里没有这个包。
- 对话页还不展示子任务摘要。那是下一步。
- 安装 extra 时，这个环境里的 `pypdf` 从 6.10.2 被装到了 `pyproject.toml` 声明的 5.9.0。这是安装命令带上的依赖对齐，不是本任务改的业务代码。
