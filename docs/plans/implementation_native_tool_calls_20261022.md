# 原生工具调用实施记录

## 授权与范围

2026-10-22用户批准原生结构化工具调用方案，决定见ADR-0020。仅修改app/llm/base.py、app/llm/openai_compatible.py、app/agents/pipeline.py、app/agents/prompts.py、scripts/run_agent_chain_eval.py、相关tests/evals、README、tasks.yaml及本计划/ADR/必要独立评审。直接调用方如发现需要额外接口调整，仅限同一工具协议目标；不改权限模型、工具执行副作用、数据库、依赖、冻结数据/阈值或CI行为。

基线：codex/eval-001-report-query，HEAD dc134455307f4cebe150b6207998fb8683013365。用户已有AGENTS/.cursor规则、治理/流程文档和tasks.yaml改动及Untitled未跟踪文件，保留。该SHA Ubuntu Backend/Frontend/RAG成功；Agent job119748029783工具指标0.5，离线报告重放agent-003为invalid_envelope，agent-004正常，不当成新增回归。原始报告data/validation/ci-39755999539/artifact/official.json。

## 实施及验证

先以新增HTTP契约用例证明当前未发送tools且不能读取tool_calls，再实现LLMOptions工具定义、客户端严格单调用解析/预算/缓存和行动/评测接线。工具schema按策略和技能交集发送。保持普通chat返回str，客户端归一为内部信封，执行器及模型调用数不变。Prompt切换为原生指令，更新相关旧fixture，不放宽拒绝断言。

使用D:/DepTooL/anaconda3/envs/ai-assistant/python.exe及scripts/validate_delivery.py隔离运行。基线专项tests/test_tool_dsml_protocol.py与tests/eval/test_agent_eval_runner.py；开发专项扩大LLM、缓存、预算、Pipeline、Supervisor、Skill、身份/租户/撤销调用方。运行Ruff/mypy及本地全量；最终SHA Ubuntu CI、真实模型复验及业务接受未完成时保持in_review。纯后端内部协议无HTTP契约变化，前端本地不适用。

独立审查需独立Agent核对真实入口、无工具收尾、多调用/畸形、预算与缓存、取消/撤权和评测一致性，记录输入指纹、发现及缺口。回滚见ADR-0020。结果、命令/退出码和最终指纹在本文件补记，不另建重复交付报告。

## 实际实现与验收

谁可以做什么：OpenAI 兼容行动请求在 `LLMOptions.tools` 非 None 时发送原生 `tools`/`tool_choice`；单个已注册 function 调用被归一为内部 `<tool_call>` 信封后走原执行链。普通文本请求（tools=None）契约不变。收尾 `tools=[]` 拒绝原生和文本工具协议。官方评测与运行时共用 `native_tool_definitions(selection_tool_registry())`。

成功：单调用严格 JSON 对象参数被执行。失败：多调用、混合文字、重复键、非有限数字、截断、未注册工具、文本 `<tool_call>`/DSML 抛 `llm_invalid_tool_call`，不修补、不降级、不新增格式重试。评测侧无效原生调用归一为 `<tool_call></tool_call>`，记 `invalid_envelope`，不重试。

实际文件：`app/llm/base.py`、`app/llm/openai_compatible.py`、`app/agents/pipeline.py`、`app/agents/prompts.py`、`scripts/run_agent_chain_eval.py`、`tests/test_llm_native_tools.py`、`tests/eval/test_agent_eval_runner.py`、`tests/test_rag_028_model_capability.py`、`tests/test_supervisor_tool_contract_review.py`、`README.md`、`evals/README.md`、`docs/adr/0020-native-tool-proposals.md`、`docs/adr/README.md`、`tasks.yaml`（仅追加 TOOL-001，保留用户 GOV-004 改动）、本文件。评测器 `agent-chain-eval-0.5.0`。冻结数据/阈值/CI 工作流未改。

验证（解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0，pytest 8.4.2，ruff 0.8.4，mypy 1.14.1，平台 win32，HEAD dc13445 dirty）：

- `scripts/validate_delivery.py targeted` 宽专项 `20261022T103837-fb32ae7838d9`：退出 1；602 passed / 1 failed（`test_official_native_invalid_tool_call_fails_without_retry`，gate 当时不接受空成功尝试）。覆盖 native HTTP、DSML 解析、评测 runner/gate、RAG-028、Supervisor 合同、Pipeline 预算、Agent 链、Skill/工具路径、Fast route、组合工具。
- 将无效原生调用归一为内部空信封后，同范围评测/HTTP `20261022T104132-9c688cb902b7`：退出 0，54 passed。未重跑前一轮其余 602 项；那些用例不经过该评测器分支。
- `ruff check .` 退出 0。
- `mypy app/` 退出 0，231 files。
- 前端不适用（无 HTTP 契约变化）。

当前指纹（SHA256）：`app/llm/base.py` d93c0384126634aceeecaceb36cd6257be082159cf495e19e578b0f917a503da；`app/llm/openai_compatible.py` 0e8c6da4cc322be04f57e0c83207e6dabc3bf65c87b95850ff712f750f4f7668；`app/agents/pipeline.py` 5fc1889340e13b55812a3268a02b868b11dc644e408ef4be87252ba6e22cc346；`app/agents/prompts.py` 90c34f241207b545713040e84ea67c1553fb77650e781e9045b2b7fcfac1b9a1；`scripts/run_agent_chain_eval.py` 35455e8283eb36fa26b9ceaf2f228dc1c9da48ac17bc438317a8e2d10927d9f4；`tests/test_llm_native_tools.py` 5b9e04fbf8b79cc0a0ed33712cae4ea1ff100d5556a8eb365ca80d23ee6b6cf4。

未做项：独立审查；本地全量 pytest；最终 SHA Ubuntu CI（含 Agent official）；付费真实模型复验；提交/推送；业务接受。身份/租户/撤销入口本轮无代码改动，未单列重跑。EVAL-001 规划中的 `0020-evaluation-job-lifecycle.md` 尚未落盘，本卡使用已批准的 `0020-native-tool-proposals.md` 同号不同文件，未改 EVAL-001。回滚：回退上述 options/客户端/行动与评测接线及 Prompt，不改数据库、不重放工具。任务保持 in_review，不得 done。

## CI 缓存回归（1858e16）

Ubuntu run 39768108317：Agent official 通过（工具指标 1.0）；Backend 失败于 `test_cache_acceptance_default_chatservice_actual_http_count`，第二次相同对话 HTTP 7→8。原因是行动阶段只要广告 tools 就整段跳过缓存，纯文本草稿也无法复用。修正为 schema 进入缓存键，仅 native `tool_calls` 响应不落盘。
