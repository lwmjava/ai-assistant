# 实现说明：不需要工具和知识库的问题只生成一次

> 日期：2026-10-01
> 计划：`docs/plans/plan_supervisor_fast_route.md`

## 完成了什么

`AGENT_ORCHESTRATION=langgraph` 时，代码先看用户原文，再决定路径。

- 简单问题：一次 `stream_chat`。不理解、不规划、不检索、不调工具、不反思、不进 Supervisor。非流式接口把同一次流式结果收成完整回复。
- 工具：只列出点名的工具，行动循环最多 2 轮。打开说法是「算一下 / 帮我算 / 计算一下 / 计算器」「运行这段代码 / 执行这段代码 / 代码沙箱」「现在几点 / 当前时间 / 今天几号」。
- 知识库：检索一次再生成一次。没有检索器，或检索失败，仍只生成一次，并告诉模型没有检索结果。打开说法是「知识库 / 根据文档 / 根据资料 / 查一下」。
- 多轮：只有「先调研 / 分步调研 / 多轮」，且没有更具体的工具或知识库说法时，才进入 Supervisor。

同时命中时，工具优先于知识库，知识库优先于多轮。

Supervisor 的调研节点改为一次模型调用，不再进入行动循环。调研最多 2 轮。撰写之后直接结束，模型反复要求撰写也只会写一次。

默认 `self` 仍走五阶段管线，检索行为不变。

## 代码位置

- `app/agents/route.py`：路径判定。
- `app/agents/fast_path.py`：简单、工具、知识库三条短路径。
- `app/agents/supervisor.py`：调研一次调用，撰写后结束。
- `app/services/chat_service.py`：只在 `langgraph` 且不是多轮时走短路径。

## 验证

```text
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_fast_route.py tests/test_supervisor.py -q --tb=short
25 passed

D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m ruff check app/agents/route.py app/agents/fast_path.py tests/test_fast_route.py
All checks passed
```

`AGENT_ORCHESTRATION=self` 时 `tests/test_chat.py` 里依赖检索来源的用例通过。同一进程里第二个流式请求会在 `sse_starlette` 的退出事件上报「bound to a different event loop」；单独跑 `test_chat_stream_returns_sse` 通过。堆栈不在短路径里。

当前环境没有 `mypy` 模块，类型检查未跑。

## 没做的事

- 没有回答缓存，所以重复问题仍要等一次模型返回。远程模型一次生成做不到毫秒，本轮去掉的是多轮编排的分钟级等待。
- 没有改记忆压缩，没有按节点换模型。
- 没有把进行中的节点改成逐 token 推送。Supervisor 仍是整图结束后再回放。
- 没有持久化分派记录。
- 没有文件读取工具，不因为「文件」二字打开工具路径。
- 没有把 Supervisor 标成已完整实现。
- 没有改 `.env`。
