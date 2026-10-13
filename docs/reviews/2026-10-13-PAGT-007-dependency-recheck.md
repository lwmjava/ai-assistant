# PAGT-007 前置 AGT-003 当前证据复核

日期：2026-10-13。复核 Agent：`/root/pagt007_dependency_check`，独立于本卡实现。

结论：**AGT-003 当前关键验收路径复核通过，支持 PAGT-007 准入；未发现该前置卡当前未决 runtime P1/P2。** 本结论来自当前源码及实跑回归，不凭 `done` 或历史官方分数判定。本报告不替代 PAGT-007 的 CI 与真实模型验收。

仓库：`E:\culture\SmartCustomerServiceSystem\ai-assistant`。当前 HEAD：`a0d02eb411a53c3191e81f0fc99528ea60d0d3c4`。工作区包含既有 PAGT-004/005/006 修改；这些修改未归为 PAGT-007 交付。本 Agent 未改源码、测试或任务卡，仅在主 Agent 通知用户批准 docs/ 扩展后写本报告，未提交、推送、合并或部署。

## 依据与当前事实

读取 AGENTS.md、任务卡 AGT-003/PAGT-007、指定 task-card-delivery v1.3.0、AI 辅助开发迭代指导与 Harness 治理有关章节、适用 Cursor rules（00、01、02、03、07、08、09、11）、`docs/plans/implementation_agt_003.md`、`docs/plans/plan_agent_chain_fix.md`，核对当前 Pipeline、运行器、数据集、调用方和回归。

| 前置原始行为 | 当前链路与观察 | 结果及证据类别 |
|---|---|---|
| 行动与最终回复保留最近历史 | `_build_act` / `_build_respond` 仍调用 `recent_dialogue`，长代码历史及上下文不覆盖断言通过 | 当前源码与 E3 单元补充通过 |
| 短路复用共享工具循环 | `run` / `run_stream` 使用 `_SHORT_PATH_POLICY`，执行 `_run_action_loop` / `_iter_action_loop` | E2 隔离 Pipeline 集成通过 |
| 短路只允许三项安全工具 | 白名单为 calculator、code_sandbox、get_current_datetime；web_fetch、MCP、未知工具拒绝，计数为零 | E2 隔离执行集成通过 |
| 用户回复“1”可续答并执行代码 | 短/全路径代码工具执行一次，工具标记不进入回答；ChatService 最终代码结果持久化 | E2 真实代码、合成 provider、测试 SQLite 通过 |
| 无工具短路维持四次模型调用 | 理解、分流、行动、最终回复计数断言通过 | E2 隔离 Pipeline 集成通过 |
| 评测提示不暴露标准答案、不执行沙箱 | dataset 校验、提示 expected 字段隐藏、沙箱替换拒执行、报告禁止覆盖均通过 | E3 自检补充，不作为真实模型质量证据 |

## 实际运行

主 Agent 授予独占 pytest 时隙，结束后已通知释放。明确指定解释器、native 后端和测试库，没有并发访问共享测试数据库。

```powershell
$env:RAG_BACKEND='native'
$env:DATABASE_URL='sqlite:///./data/test_ai_assistant.db'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_agent_chain.py tests/eval/test_agent_chain_dataset.py tests/test_llm_route.py tests/test_tools.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt007-dependency-recheck
```

退出码 **0**；**71 passed、0 failed、0 skipped，3.03 秒**。1 条既有 warning：`app/agents/tools/sandbox/sandbox.py:93` 的 `ast.NameConstant` 弃用提示。未运行全量、ruff、mypy、前端或真实外部模型，本报告只复核前置关键路径。

## 审查检索及历史证据限制

实际执行 `rg -n 'AGT-003' docs/reviews`，未找到 AGT-003 专属未决审查；结果主要是 PAGT-003 文本包含子串，未把它们误认作 AGT-003 缺陷。读取前置实施说明和原拆分计划，未见 AGT-003 当前未决 runtime P1/P2。另核对后续 PAGT-006 修复及验收结论：历史 P1/P2 不应仅凭旧报告标题继续认定为当前未决，但本报告不重新代验 PAGT-006 整卡。

旧实施说明的测试失败、mypy 错误和 official 分数均是当时证据。当前 71 条通过不能推导全量通过，也不证明模型质量。历史 official 报告 `evals/reports/agent-chain-v0.1-official-20260927T132731Z.json` 使用 deepseek-chat、temperature 0、evaluator 0.2.0，三项指标 1.0；数据集 SHA256 与当前相同，但 Pipeline、Prompt 和上下文已有变化，该报告作为 **E4 历史参考**，不能替代 PAGT-007 当前 CI E1。十例 Silver、两条工具选择样本只具有有限硬退化判别力，本卡原始非目标禁止自行扩容。

## PAGT-007 运行器自身关注项

这些是本卡实施应处理或准确限定的门禁可信性问题，不作为 AGT-003 当前 runtime 回归失败：

- `llm_availability()` 明确只读取 chat 链配置，运行器却调用 intent provider；应增加“chat real、intent 无真实 hop/Mock”的拒绝反例，实际核对送模 provider 身份。
- 当前 `prompt_hash` 只覆盖 `SYSTEM_PREFLOW`，未覆盖 `SYSTEM_ACT`、工具名单或实际工具选择请求；版本报告应覆盖实际送模内容和构建身份。
- 当前 `SIDE_EFFECTS` 是固定零字典，不是动态副作用监测。评测代码不执行工具不等于真实模型网络请求为零；报告需明确网络分类与证据性质。
- 当前工具评分只检查两条正例 expected_tools recall，对没有 expected_tools 的例不惩罚额外选择；不能据此宣称负例或工具执行安全已评估。保留原始十例，不把扩容作为本卡实现。

## 实跑时关键文件 SHA256

下表于该次回归后只读采集。后续本卡修改运行器或 Pipeline 时，最终关闭必须重新核对受影响指纹与路径，不能沿用本表关闭新代码。

| 文件 | SHA256 |
|---|---|
| app/agents/pipeline.py | b0fc989e742526d23f5089192581d45d1dd8116f50d5f8a230cefb8d84626e95 |
| app/agents/prompts.py | 168da056cc188e63e6edd741119644bfc22d4d1c00827433a999b9c4210dba51 |
| app/agents/tools/base.py | f53a0d32bf0991a70f2ec9c718874e7855bd5376993e01cdd546a016d9b6ead9 |
| app/agents/tools/executor.py | dfd8203bf477ad0709a0fcbdd081c75a2313eb8a3756aa3135334424c3f233e6 |
| app/agents/tools/builtin.py | 74251a7354f1fa9c17ac4d900ede16de36de06e06ac975bfbfb113eeb90b7829 |
| app/services/chat_service.py | 616ad0d7db6d4c371246547a1ccab35b957b02349b1cc91725104d7fbe947868 |
| scripts/run_agent_chain_eval.py | 6fc955f61d74b1fb8a06ef7c7bdc5af1cced2e9510d958c86c4546f81c43f6a5 |
| evals/datasets/agent-chain-v0.1/cases.json | 69899cc091e4ee28cbc1972cce3d210c61510f594aa11547f3e23ac608dfb1d6 |
| evals/datasets/agent-chain-v0.1/index.json | 6f0b82aaac2f08cb2bcd397057975527e4615fec7368ffe018de0fb9915ecebb |
| tests/test_agent_chain.py | ea98677566a16876b4ea579fe7629e7cdab700aa8e12dc67c64276fc6a6530c0 |
| tests/eval/test_agent_chain_dataset.py | 013caa459fa9ed3ed92c07a9a31d2d0d5997a556c4c7f46108a06541bb8a390a |
| tests/test_llm_route.py | 6f9b274dde2dfe6789d0755b23c59d6967e9b5c7f832547083a6cfe5fa83eda0 |
| tests/test_tools.py | 13a8f81f2dadc6068d5da82dacc26dbb279b7fd5a79f87dbb2a23a95e3cbeebb |
| tests/conftest.py | 00ffb3da933e6107ce9a25c47b32909e316eb7566b6151e198dba63ca1d1930c |
