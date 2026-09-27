# 实现说明：短路路径带上历史，并执行安全工具

## 完成的行为

行动和最终回复都会带上「最近对话」。完整流程和短路都使用同一段截取结果。这段历史单独成节，不写入检索上下文。

短路仍跳过规划、检索、质量门和反思，但会进入共享工具循环。提示词和执行层只允许 `calculator`、`code_sandbox`、`get_current_datetime`。`web_fetch`、名称以 `mcp__` 开头的工具以及其他工具不会出现在短路提示里，模型即使写出调用也会被拒绝。

上一轮给出选项、用户回复「1」时，分流无论走完整流程还是短路，都会执行 `code_sandbox`，并把代码结果交给会话服务落库。没有工具调用的短路，模型调用次数仍是理解、分流、一次行动和最终回复这四次。

## 代码位置

- `app/agents/pipeline.py`：`_build_act`、`_build_respond` 增加最近对话。短路调用 `_run_action_loop` / `_iter_action_loop`，策略是 `_SHORT_PATH_POLICY`。`build_tool_choice_messages` 只列出上述三个工具。
- `evals/datasets/agent-chain-v0.1/`：两条续答用例增加 `expected_tools`。
- `scripts/run_agent_chain_eval.py`：正式模式在分流之外再请真实模型选择工具，只解析输出，不执行工具。

## 验证

```text
pytest tests/test_agent_chain.py tests/test_supervisor.py tests/test_tools.py tests/test_llm_route.py tests/test_agent.py tests/eval/test_rag006_pipeline.py tests/test_chat.py tests/test_chat_controls.py tests/test_workflow.py tests/test_sandbox.py tests/eval/test_agent_chain_dataset.py -q
129 passed；3 个流式事件循环失败与修改前相同
pytest -q
423 passed，2 skipped，11 failed
ruff check app/agents/pipeline.py tests/test_agent_chain.py tests/eval/test_agent_chain_dataset.py scripts/run_agent_chain_eval.py
All checks passed
mypy app/
84 个错误。修改前是 85 个。本次去掉了历史参数的列表类型不兼容，没有新增错误
python scripts/run_agent_chain_eval.py --mode smoke
evals/reports/agent-chain-v0.1-smoke-20260927T132725Z.json
python scripts/run_agent_chain_eval.py --mode official
evals/reports/agent-chain-v0.1-official-20260927T132731Z.json
模型 deepseek-chat，temperature 0，seed_supported false
route accuracy 1.0，follow-up recall 1.0，tool selection recall 1.0
side_effects 中沙箱、MCP、网络、检索和数据库均为 0
```

全量失败与上一轮相同：3 个流式事件循环错误、4 个缺少表格或演示文稿解析库的测试、删除文档清理向量，以及 3 个 BM25 全 0 用例。

## 没做的事

- 没有改理解阶段、检索查询、不可信资料拦截、Supervisor 和前端。
- 没有建立完整的工具风险模型。
- 没有在页面上实际发送「1」并查看代码结果卡片。真实模型评测已经选出 `code_sandbox`，但那次评测没有执行沙箱。
