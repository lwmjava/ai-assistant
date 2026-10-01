# 实现说明：共享工具循环与执行护栏

## 完成的行为

完整流程里的行动、质量门重跑和 Supervisor 调研共用同一个工具循环。循环会发出 `tool` 和 `code_result`，并把草稿写进当前请求状态。一次性接口和 Supervisor 仍拿到字符串草稿。

同一次请求里，工具名和排序后的参数相同的调用只执行一次。第一次发现重复时不再执行，留下「相同参数已执行过，请直接使用已有结果」，然后立刻收尾。坏格式按一次失败处理，工具不执行。轮次用完或需要收尾时，再做一次不列工具的行动；如果模型仍输出调用指令，草稿改成「工具调用次数已用完，未能得到最终结果。」轮次上限为 0 时不执行工具，但仍做这一次行动，草稿不为空。

提示词里的工具清单和执行前校验使用同一份允许名单。名单为空表示沿用当前注册的全部工具。简单回答这条路径本轮仍只做一次行动，不进入工具循环。

一次性接口的 Trace 会记录工具执行、跳过和轮次用完。工具事件只有名称、状态、轮次和耗时。语言模型预览和异常说明会先去掉手机号、密钥和主机路径；清理失败时写入「（追踪内容已省略）」。流式接口仍然不写 Trace。

## 代码位置

- `app/agents/pipeline.py`：`ExecutionPolicy`、`_iter_action_loop`、`_run_action_loop`。请求状态上的 `executed_tool_fingerprints` 在质量门重跑时继续有效。流式质量门改为转发同一循环的 `tool` 事件。简单回答仍调用 `_run_action_once`。
- `app/agents/tools/base.py`：`inspect_tool_call` 区分没有调用、有效调用和格式错误。`parse_tool_call` 只返回有效调用。
- `app/debug/trace.py`：`sanitize_trace_text`；工具事件不再保存参数和结果。

## 验证

```text
pytest tests/test_agent_chain.py tests/test_supervisor.py tests/test_tools.py tests/test_llm_route.py tests/test_agent.py tests/eval/test_rag006_pipeline.py tests/test_chat.py tests/test_chat_controls.py tests/test_workflow.py tests/test_sandbox.py -q
118 passed；3 个流式事件循环失败与修改前相同
pytest -q
415 passed，2 skipped，11 failed
pytest tests/eval/test_agent_chain_dataset.py 含在全量中，未出现在失败列表
ruff check app/agents/pipeline.py app/agents/prompts.py app/agents/tools/base.py app/debug/trace.py tests/test_agent_chain.py tests/eval/test_agent_chain_dataset.py scripts/run_agent_chain_eval.py
All checks passed
mypy app/
85 个既有错误，本次没有新增
python scripts/run_agent_chain_eval.py --mode smoke
evals/reports/agent-chain-v0.1-smoke-20260927T125447Z.json
route accuracy 1.0，follow-up recall 1.0
python scripts/run_agent_chain_eval.py --mode official
evals/reports/agent-chain-v0.1-official-20260927T125654Z.json
模型 deepseek-chat，temperature 0，seed_supported false
route accuracy 1.0，follow-up recall 1.0
python tests/test_debug_smoke.py
10 项通过
```

全量失败与上一轮相同：3 个流式事件循环错误、4 个缺少 `openpyxl` 或 `pptx` 的解析测试、删除文档清理向量，以及 3 个 BM25 全 0 用例。这些路径不在本次工具循环改动里。

## 没做的事

- 没有让简单回答进入工具循环，也没有给行动和最终回复补历史。
- 没有修改 `AGENT_MAX_TOOL_ROUNDS` 的默认值。
- 没有做跨请求熔断，没有改 Supervisor 的事件接口，没有给流式接口补 Trace。
- 没有在页面上发送「1」并确认代码工具卡片。这要等简单回答也能执行安全工具之后再验。
