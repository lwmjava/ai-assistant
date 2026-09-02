# 工具调用（Function Calling）能力 — 完成概述

在 `tool-calling` 分支（基于 `rag-retrieval`，提交 `e3ec6e0`）实现了 Agent「行动」阶段的工具调用能力，让 Agent 能声明并调用外部工具 / API。

## 主要改动

**新增文件**
- `app/agents/tools/base.py`：`Tool`（名称/描述/JSON Schema 参数/执行函数）、`ToolRegistry`（注册、描述、执行）、`parse_tool_call`（解析 `<tool_call>{...}</tool_call>` 信封）。
- `app/agents/tools/builtin.py`：内置工具集 `default_tools()`：`calculator`（安全四则运算，AST 白名单）、`get_current_datetime`、`web_fetch`（HTTP GET，外部 API 接入）。
- `app/agents/tools/__,init__.py`：模块导出。
- `tests/test_tools.py`：解析、执行、管线集成（含流式 tool 事件）等用例。

**修改文件**
- `app/agents/pipeline.py`：为 `AgentPipeline` 增加 `tools` 参数；「行动」阶段改为工具循环（最多 `AGENT_MAX_TOOL_ROUNDS` 次），解析到工具调用则执行并回灌结果，否则产出草稿；流式模式新增 `tool` 事件。
- `app/agents/prompts.py`：行动提示词约定工具调用信封格式。
- `app/core/config.py`：新增 `AGENT_MAX_TOOL_ROUNDS`（默认 5）。
- `app/services/chat_service.py`：构建默认工具注册表并注入管线。
- `app/api/routes/chat.py`：新增 `GET /api/chat/tools` 暴露可用工具清单。
- `.env.example` / `docs/plans/后端实现说明.md`：补充配置项与模块说明。

## 关键决策
- 采用「提示词 + 信封解析」协议（而非依赖具体 LLM 原生 Function Calling API），与 OpenAI/Ollama/Mock 等任意提供商兼容，离线可测。
- 工具循环以 `AgentState.tool_results` 累积观测，回灌到「行动」阶段提示，符合现有五阶段管线结构。

## 验证
- `pytest`：**31 passed**（含 8 个新增工具调用测试）。
- 分支 `tool-calling` 已本地提交，尚未推远程、未合 main（待审核）。

## 下一步
- 用户审核后合并 main（`git checkout main && git merge tool-calling`）。
- 可选：扩展更多业务工具、接入原生 Function Calling（如 OpenAI `tools` 参数）以提升真实模型下的可靠性。
