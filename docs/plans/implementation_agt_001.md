# 实现说明：意图分流看见上一轮

## 完成的行为

历史消息进入记忆压缩、理解、意图分流和异步反思前会先脱敏。分流请求包含最近对话；模型回复只有开头一个词是 `NO` 时才走简单回答，`YES` 和无法识别的回复都走完整流程。

数据库里的原文没有改写。行动和最终回复本轮仍不带历史，短路路径仍不执行工具，这两项属于后续任务。

## 代码位置

- `app/services/chat_service.py`：`sanitize_history_text`，供历史窗口、记忆压缩和反思序列化使用。它依次调用输入过滤和日志脱敏，不限流、不检测注入；失败时返回「（历史内容已省略）」。
- `app/agents/pipeline.py`：`recent_dialogue`、`build_preflight_messages`、`preflight_needs_full_pipeline`。最近一轮最多各 2000 字，更早消息最多 300 字，内容预算 4000 字，超长保留首尾。
- `app/agents/prompts.py`：分流说明要求把上一轮选项或确认继续判断为需要完整流程。
- `evals/datasets/agent-chain-v0.1/`、`evals/schemas/agent_chain_case.schema.json`、`scripts/run_agent_chain_eval.py`：10 条 Silver 用例。正式评测只调用真实意图模型，报告拒绝覆盖。

## 验证

修改前相关测试：83 通过，3 个流式事件循环失败。`mypy app/` 为 85 个既有错误。

修改后：

```text
pytest tests/test_agent_chain.py tests/eval/test_agent_chain_dataset.py tests/test_supervisor.py tests/test_tools.py tests/test_llm_route.py tests/test_agent.py tests/eval/test_rag006_pipeline.py tests/test_chat.py tests/test_chat_controls.py tests/test_workflow.py tests/test_sandbox.py -q
105 passed；上述 3 个流式失败仍在
pytest -q
399 passed，2 skipped，11 failed
python scripts/run_agent_chain_eval.py --mode smoke
evals/reports/agent-chain-v0.1-smoke-20260927T122423Z.json，route accuracy 1.0，follow-up recall 1.0
python scripts/run_agent_chain_eval.py --mode official
evals/reports/agent-chain-v0.1-official-20260927T122459Z.json
模型 deepseek-chat，temperature 0，seed_supported false
route accuracy 1.0，follow-up recall 1.0，10/10
```

全量失败中的 3 个流式错误与修改前相同。文档解析的 4 个失败是缺少 `openpyxl` 或 `pptx`。删除文档和 BM25 的失败在撤回本次业务改动后仍然出现，属于既有环境或数据状态问题。`mypy app/` 仍是 85 个既有错误。`ruff check` 在本次新增代码上没有新错误，原有 `AgentTrace` 和 `SecurityContext` 前向引用报错仍在。

## 没做的事

- 没有让行动和最终回复携带历史。
- 没有让简单回答执行工具。
- 没有改工具循环、Trace 或检索查询。
- 没有在页面上实际发送「1」做人工验收。
- 提供商不支持 seed，正式报告已记录该限制。
