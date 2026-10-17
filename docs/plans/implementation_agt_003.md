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

## 2026-10-13 Review F1：空行动输出失败处理

同步和流式 Pipeline 的短路、文本兼容行动以及预算收尾，收到空串或纯空白时现在返回既有通用失败提示，设置 `state.error=EMPTY_ACTION_OUTPUT`，向 checkpoint 提交 `status=error`，不再进入最终回答生成。流式以 `error → done` 结束，没有最终回复 token；done 仅表示流结束。已执行工具的结果和调用指纹保留，不重复执行或扣除调用预算。合法无工具草稿仍正常完成，短路仍为四次模型调用。

实现位于 `app/agents/pipeline.py` 的 `_stage`：在 Skill 注入前固定 `is_action_stage`，结构化输出保持既有 `INVALID_STEP_OUTPUT` 校验，其他行动空输出抛固定错误。独立复核发现初版比较追加 Skill 后的 system 文本会绕过校验，补充两项正式失败测试后修复。没有增加重试、工具强制调用、权限或状态存储边界，也没有修改 Prompt、API、配置或冻结数据集。

确定性 Observed Regression 版本 `agt003-empty-action-v1` 落在 `tests/test_empty_action_reply.py`，17 项涵盖空串/空白、同步/流式、短路/文本兼容、Skill 注入、预算收尾、已执行工具、正常无工具和结构化兼容。两项使用隔离的内存 SQLite 和真实 AgentStateService，在新会话回读 checkpoint，确认持久状态为 error。

实际验证使用 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，pytest 设置 `RAG_BACKEND=native`、`DATABASE_URL=sqlite:///./data/test_ai_assistant.db`：

```powershell
python -m pytest tests/test_empty_action_reply.py tests/test_agent_chain.py tests/eval/test_agent_chain_dataset.py tests/test_llm_route.py tests/test_tools.py tests/test_pipeline_budget.py tests/test_structured_plan_runtime.py tests/test_plan_checkpoint_consistency.py tests/test_plan_tool_outcomes.py tests/test_skill_manager_versions.py tests/test_skill_versioning.py -q -p no:cacheprovider --basetemp=data/agt003-empty-final --junitxml=data/agt003-empty-final.xml
python -m ruff check app/agents/pipeline.py tests/test_empty_action_reply.py --no-cache
python -m mypy app/ --cache-dir data/mypy-agt003-empty
```

最终结果：240 passed / exit 0 / 5.84 秒；ruff exit 0；mypy 205 源文件无问题 / exit 0。pytest 有既有 ast.NameConstant 弃用警告。红绿证据：初始新测试 13 failed、2 passed / exit 1；初版修后 86 passed / exit 0；调用方回归 180 passed / exit 0；Skill 绕过追加反例 2 failed / exit 1，修正后包含在最终 240 passed 内。调用方 180 项在最后的阶段身份修正之前执行，不冒充最后版本全量验证。

独立 Agent 只读复核最终实现，19 项无数据库入口断言通过、两个 probe exit 0，覆盖 Skill、各类空白、同步/流式、结构化错误、预算和 checkpoint 存储失败降级，未发现剩余 F1 阻断项。独立复核未调用真实模型、网络或沙箱；SQLite 持久状态由上述正式测试验证。

此次修改映射为 F1 → pipeline 失败校验、正式回归、既有计划、本文、evals/README.md 与 tasks.yaml。回滚仅撤回本次阶段身份和空行动校验及对应测试，不回退工作区其他任务的改动。未提交、推送、部署或调用真实模型；未执行全量 pytest、页面验收。模型仍可能返回空回复，本修复证明失败被识别，不证明工具选择质量提升。F2（recording fake/参数核验）与 F3（冻结用例缺少前文代码）仍待处理，AGT-003 保持 in_review，不据此关闭整卡或 PAGT-007。

## 2026-10-13 Review F3：补齐失败用例的前序代码

用户批准新数据版本及 allowed_paths 扩展后，新增 `evals/datasets/agent-chain-v0.1.1/cases.json`、`index.json`。agent-004 历史先给出生成请求和完整冒泡排序代码，再保留原执行请求与确认提示；最新「好的」和期望 full/code_sandbox 不变。其他九例仅更新版本字段。依旧十例 Silver、两例关键续答、两例工具选择，阈值仍为 0.9/1.0/1.0。旧 0.1.0 和已有失败报告保持原状。

首个代码示例超过现有较早历史的 300 字上限，正式输入完整性测试失败；最终 assistant 内容缩为 240 字，复制输入并排序，打印 `[1, 2, 4, 5, 8]`。修复不改变运行时历史裁剪、Prompt 或模型配置。完整代码在真实 Prompt 构建函数生成的分流和工具选择输入里均可见，不包含标准答案字段。测试只在隔离子进程运行已知合成 Python 示例，不调用真实沙箱或外部系统。

`scripts/run_agent_chain_eval.py` 指向 0.1.1；它与 `scripts/agent_gate_check.py` 独立冻结新指纹。cases SHA-256 为 `77ff27c8c67c68afb55eed51384e20dd07171b1d047de6cde346c976f38f5ad6`，index 为 `929e4114ef2f548de0e2d89e3d28f8ebb174f57caee39273c911fd3518c58251`（UTF-8、CRLF 规范为 LF）。新报告绑定新版本；旧 version/cases/index 身份均被拒，不把旧报告当作当前质量证据。评分算法、门禁阈值和 evaluator 协议未改。现有 CI 将通过相同脚本入口使用新输入，本轮没有触发 CI。

新增 `tests/eval/test_agent_chain_f3.py` 五项回归，验证旧冻结身份、新旧精确差异、代码可执行及送审完整性、两处新身份、旧报告绑定拒绝。数据契约测试覆盖新旧两个版本；runner 的换行测试改为读取当前候选文件验证 LF，已有跨平台规范化 hash 检查保留。evals/README.md、既有计划和 tasks.yaml 同步用户批准与版本说明。F3 已闭合，F2 recording fake/参数验证仍待处理，整卡保持 in_review。

实际命令使用指定 conda 解释器，pytest 设置 RAG_BACKEND=native 和测试数据库：

```powershell
python -m pytest tests/eval/test_agent_chain_f3.py tests/eval/test_agent_chain_dataset.py tests/eval/test_agent_eval_runner.py tests/eval/test_agent_gate.py tests/eval/test_agent_gate_entry.py tests/eval/test_agent_ci_endpoint_capture.py -q -p no:cacheprovider --basetemp=data/agt003-f3-final --junitxml=data/agt003-f3-final.xml
python -m pytest tests/eval/test_agent_chain_f3.py tests/eval/test_agent_chain_dataset.py -q -p no:cacheprovider --basetemp=data/agt003-f3-schema-final
python -m ruff check scripts/run_agent_chain_eval.py scripts/agent_gate_check.py tests/eval/test_agent_chain_f3.py tests/eval/test_agent_eval_runner.py tests/eval/test_agent_chain_dataset.py --no-cache
```

结果：最终组合 102 passed / exit 0 / 160.51 秒；随后补充新旧 Schema 参数化的最终回归 10 passed / exit 0 / 2.92 秒（与组合有重复，不合计为 112 个不同测试）；ruff exit 0。基础数据测试 4 passed；新增红测试 4 failed、1 passed / exit 1，其中两项是新版本尚不存在；中间组合 7 failed、95 passed，含历史截断及同步修订时进程仍持旧身份的结果，不用于最终验收。首个文件写入脚本遭权限拒绝，改受控补丁写入；最终换行整理经工具授权完成。初次 ruff 报修改测试后的未使用 import，移除后通过。

独立只读 Agent 无数据库 probe exit 0：确认旧指纹、新旧精确差异、完整代码进入两类 Prompt、两处最终指纹一致、旧绑定逐项被拒及旧数据路径被 frozen_contract 拒绝，未发现阻断项。中途一次读到未同步 hash，最终版本复验已闭合。没有运行模型、网络或工具，结论仅为输入和门禁完整性。

未执行真实 official、全量 pytest、页面验证或 Git 提交/推送/部署。不能据此宣称真实模型召回提升或 PAGT-007 达标；0.1.0 的既有失败继续有效。回滚可将 evaluator/gate 恢复旧路径和旧指纹，保留两个版本与历史报告；不得用降低阈值或删除失败用例回滚。

## 2026-10-13 Review F2：参数校验与内存记录工具

用户批准方案、协议及 scripts/agent_gate_probe.py 范围扩展后，工具评测不再只检查工具名：模型看到与 runtime 短路相同的行动 Prompt、真实工具描述和参数 Schema；同一 action_user_content 构建函数由 runtime _build_act 与评测调用，原 runtime 内容逐字保留。selection_tool_registry 仅复制生产元数据，函数替换为拒绝执行占位；通过校验后另建内存 Tool callback 实际记录一次，没有真实工具函数可达。没有增加模型请求或重试，official 仍为十次分流与两次行动。

调用必须可解析、在三工具白名单且参数符合实际生产 Schema 的支持子集。校验拒绝重复 JSON 键、非有限数、类型错误、空白 code/表达式、非正 timeout 和坏 Python 语法；纯注释代码没有有效语句也失败。未知 Schema 校验关键字失败关闭，不新装依赖。code_sandbox 仅 AST 解析，agent-004 必须匹配前序代码的 AST，允许注释/格式变化。agent-003 没有固定前序代码，验证只覆盖语法和参数，不能证明生成算法正确，也不替代沙箱的安全策略。合法代码文本即使包含危险操作也仅被记录，不执行。

report/gate 升为 2.0.0，evaluator 升为 0.3.0。保留原三项指标和 0.9/1.0/1.0 门槛，新增 tool_call_validity_recall >= 1.0。逐例 tool_record 有意图、工具名、参数有效性、固定错误码、参数摘要 SHA-256、recording_attempts/recorded_calls 与 recording_result；invalid 不尝试 callback，valid 才通过 Tool.execute 调用内存 callback。recorded 仅指记录成功，没有伪造 stdout/code_result 或声称沙箱执行。报告仍保留原始模型回复，不新增参数原文副本。

gate 从 raw 工具回复与当前输入复算参数和前序绑定，严格比对字段类型（bool 不能冒充整数）、计数和摘要，再独立累计有效命中，拒绝自报计数和汇总伪造。其回放契约可验证一致性，不能证明第三方报告确实执行 callback，也不是密码学真实性证明。受控测试中两个同名空参数提议原 selection=1.0、validity=0.0，isolated official 和 gate 均 exit 1。未知或禁止工具、错误前序代码均未进入 callback。

agent_gate_probe 新增 arguments 退化探针：同名空参数维持 selection=1.0、validity=0.5，只触发有效调用率；无工具提议同时降低两项，不能为了保留单一失败原因放松新门禁。旧冻结 0.1.0/0.1.1 和已有报告不改；index 的 evaluator_version 是数据发布时初始版本，新报告独立声明当前版本。旧协议拒绝进入新版验收。Schema 对两版 dataset_version 明确枚举，实际两版案例通过同一 Schema 子集及 Pydantic 校验；修复此前 Schema 只允许 0.1.0 的漂移。

修改映射：pipeline.py 的共享行动构建与安全元数据注册表；run_agent_chain_eval.py 的参数校验、fake 执行记录、报告和 fourth metric；agent_gate_check.py 的明细绑定和独立有效计数；agent_gate_probe.py 的第四类探针；tests/eval/ 下对应契约、runner/gate/CLI 及 F3 兼容测试；Schema、evals/README.md、既有计划和 tasks.yaml 对账。

验证使用 D:\DepTooL\anaconda3\envs\ai-assistant\python.exe，pytest 设置 RAG_BACKEND=native、DATABASE_URL=sqlite:///./data/test_ai_assistant.db。实际命令：

```powershell
python -m pytest tests/eval/test_agent_recording_tools.py tests/eval/test_agent_chain_dataset.py tests/eval/test_agent_chain_f3.py tests/eval/test_agent_eval_runner.py tests/eval/test_agent_gate.py tests/eval/test_agent_gate_entry.py tests/eval/test_agent_ci_endpoint_capture.py tests/test_agent_chain.py tests/test_empty_action_reply.py tests/test_pipeline_budget.py tests/test_structured_plan_runtime.py tests/test_plan_tool_outcomes.py tests/eval/test_rag006_pipeline.py -q -p no:cacheprovider --basetemp=data/agt003-f2-final --junitxml=data/agt003-f2-final.xml
python -m ruff check app/agents/pipeline.py scripts/run_agent_chain_eval.py scripts/agent_gate_check.py scripts/agent_gate_probe.py tests/eval/test_agent_recording_tools.py tests/eval/test_agent_chain_f3.py tests/eval/test_agent_chain_dataset.py tests/eval/test_agent_eval_runner.py tests/eval/test_agent_gate.py tests/eval/test_agent_gate_entry.py --no-cache
python -m mypy app/ --cache-dir data/mypy-agt003-f2
```

最终结果：265 passed / exit 0 / 143.94 秒，1 条既有 ast.NameConstant 弃用 warning；ruff exit 0；mypy 205 源文件无问题 / exit 0。基础 10 passed / exit 0。新增初版红测试 18 failed / exit 1，主要是缺少新增记录 API、返回契约与 Prompt Schema，不能全部称为原业务断言红；实现后 18 passed。扩充到 32 项 recording 反例（包含在最终 265 项）：完整 isolated official 的请求数、参数错误、前序绑定、危险代码只记录、旧协议、伪造明细/汇总。中间契约组合 1 failed、62 passed，原因是旧冻结输入缺前序代码，已将当前协议的正向 fake 记录测试指向当前数据；旧版字节和失败证据仍由 F3 回归保护。ruff 首次一条长行已拆分，最终通过。

独立只读 Agent 两个无数据库 probe exit 0，无阻断项：12 个无效参数不进 callback、合法提议实际一次 Tool.execute、替代前序代码拒绝、危险代码文本未执行、runtime 与 eval Prompt 逐字一致；同名空参数低有效率 gate 拦截、四类字段伪造 record_binding、汇总伪造 metric_integrity、第四类探针、冻结身份与协议检查均通过。生产函数设为失败哨兵而未触发；未 pytest、模型、网络、沙箱或文件修改。

本轮未调用真实模型、未执行 full pytest/页面/生产验证，未 Git 提交/推送、CI 或部署。F1/F2/F3 实现缺口已修复；AGT-003 保持 in_review，仍需新版真实 official 和人工业务证据，PAGT-007 不能用这些模拟结果关闭。回滚撤回本轮 evaluator/gate/probe 协议和构建函数提取，保留 F1/F3 数据修复与历史证据；回滚旧协议不能作为新版验收通过。


## 2026-10-14 新协议真实 official 执行补记

用户明确授权使用.env现有模型/密钥/API，当前实际单一intent provider请求deepseek-flash、护栏批准、缓存关闭。指定conda解释器执行一次12请求、温度0/60秒单次上限/无新增重试；排他新报告[原始结果](../../evals/reports/agent-chain-v0.1-official-20261014T022048326800Z.json)使用dataset0.1.1/evaluator0.3.0/report2.0.0。分流/续答/工具选择均1.0，工具调用合法率0.5低于原1.0门槛，official与独立身份gate均exit1，agent-004为prior_code_mismatch且recorded_calls=0。报告与失败保留，未重复请求筛选通过。

本轮无业务源码/Prompt/冻结语料/阈值变更；AGT-003真实official已执行但不通过，PAGT-007前置和远程CI必交付仍不满足，两卡保持in_review。无CI触发或Git发布，真实模型质量失败不能由人工认可消除；当前服务端返回模型身份不可核验，只记录请求模型。报告side_effects是recording设计声明，真实模型HTTP流量存在，不把network=0解释成未拨网络。执行机器记录见[执行记录](../reviews/agt003_official_execution_20261014.json)。


## 2026-10-14 已确认代码保持修复实际交付

已登录用户要求运行先前代码或确认执行时，行动提示与结构化规划提示共用原代码保持规则：原样传入arguments.code，不添加输出前缀/额外打印/包装/优化；明确修改要求仍允许修改，来源不清或代码不完整提示澄清。默认完整链规划新增最近原历史，避免只依据理解摘要重新生成代码；只有sandbox可用且预算未耗尽时提示保持规则。规则只是Prompt语义，不提供调用授权、不自动替换模型参数；原Guard/Executor/白名单/权限/预算与评分不变。完整/短路以及默认结构化完整run/stream均已验证提示/参数传播。

实际修改：app/agents/pipeline.py共享_confirmed_code_guidance和_build_plan输入、tests/test_agent_chain.py入口/预算反例、tests/eval/test_agent_recording_tools.py打印前缀Observed Regression，evals/README.md回归登记。独立计划审查发现“仅行动提示绕过默认结构化完整链”P2，实施前修订计划并一并闭合；独立17入口断言通过，无新未决P1/P2。用户明确批准扩展docs/ai-prompts/两登记文件，追加prompt-model-v4并保留旧v1/v2/v3，version_registry check退出0；34项HEAD既有登记漂移同步不代表重新发布或验收这些历史功能。

修复前最终有效红证据4failed/7passed（两个action、两个默认完整run/stream缺规则/原历史）；测试脚本一度重复定义、流式方法名误写及忽略代码围栏末端换行均如实保留在过程证据，非业务根因。最小实现后相关142passed/exit0，Ruff范围exit0，mypy app/205源exit0。

真实模型用现有.env、请求模型deepseek-flash，修后第1轮12请求，official/gate退出0、四指标均1.0；agent-004先前代码AST保持，recording只调用内存替身。四类route/followup/tool/arguments派生probe正确目标退出1，probe总体exit0，没有新增模型调用。用户最多3轮，本轮只用了1轮（此前未修复基线不计），不追加第2/3轮。新报告与输入身份见evals/reports/agent-chain-v0.1-official-confirmed-code-r1-20261014.json及docs/reviews/confirmed_code_rounds_20261014.json。报告SIDE_EFFECTS为设计声明而非动态计数，真实模型HTTP存在，服务端实际返回模型身份未核验。

首轮全量2149passed/79failed，511.96秒exit1：79集中在CLI断言，子进程stdout为UTF-8而父进程默认GBK解码，91warnings中有UnicodeDecodeError线程警告，导致stdout为None或乱码。没有改业务断言/源码，通过同一指定conda解释器开启PYTHONUTF8=1，集中复验81passed/exit0（覆盖79失败及两同文件既有用例）。该81不是整仓通过，本轮全量UTF8重验结果另补。初次Prompt版本check失败已在正式追加v4后通过，未删除既有漂移证据或放宽检查。

未修改.env、评分/冻结语料/阈值/执行器、权限或State持久化，未调用真实沙箱/MCP/RAG/业务数据库（正式评测只recording）；测试使用隔离SQLite，禁用模型凭据后执行。无Git提交/推送/CI触发/合并/部署；PAGT-007仍须真实GitHubCI，不能拿本地结果关闭。Prompt约束不是安全强制验证，不保证所有模型永久遵循；样本为Silver小集，不宣称生产质量/性能/PostgreSQL/多实例或断电exactly-once。回滚仅撤本轮Prompt及配套测试，登记须与完整源码匹配，保留所有真实失败报告。最终关闭须全量结果/独立最终验收与任务备注同期。


### 本轮关闭契约对账（等待最终全量/独立裁定）

| 原始要求 | 当前证据 | 结果/边界 |
|---|---|---|
| 行动与最终回复带最近对话、1500字代码完整 | 142专项及17独立入口，_build_respond未改，行动/规划原历史保留 | E2/E3通过；真实生成指标另记 |
| 短路复用共享工具循环、完整/短路code_result落库 | 原主链专项已在142中执行，最终全量同时覆盖，另补新进程新Session提交回查 | E2相关路径通过，回查结果随后补记 |
| 三工具白名单、未知/MCP/web_fetch拒绝 | 原白名单/拒绝正式回归与17独立入口，无执行边界变更 | E2通过，不承诺未分级MCP可用 |
| 无工具短路模型调用数不变 | 原test_short_path_without_tool_keeps_call_count本轮执行 | E2通过 |
| 不可信资料点名工具仍拦截 | 原正式拒绝回归，静态提示明确不提供调用授权 | E2通过；Prompt不是新的安全Guard |
| 真实official与参数绑定、内存recording | 本轮r1四指标1.0/official及独立gate0、agent004原代码AST保持、四类fault gate目标exit1 | E1模型生成+E2机制通过；网络存在，side_effects设计声明不是动态计数 |
| 报告新文件拒绝覆盖 | r1排他新路径、原失败基线未改；正式runner排他写入回归与新鲜probe | E2通过，不覆盖历史 |
| 非目标与版本追踪 | 仅pipeline Prompt/历史、测试/文档，评分/数据/Guard不改；v4追加旧历史不变/check0，官方source/Prompt hash绑定 | 通过；无Supervisor/前端/权限/新Harness/生产变更 |
| 独立审查与最终人工业务验收 | confirmed_code_review计划P2已闭合/17入口0；独立confirmed_code_final_accept原契约/真实报告/registry复算通过 | 等待最终全量后出整卡裁定，当前不提前done |
| 全量与失败归因 | 首全量2149/79/91warning，UTF8定向81通过，不改断言；第二全量运行中 | 结果随后补记，不能以子集代整仓 |

用户当前修复/最多三轮及先前明确人工验收委托均已登记；Prompt版本两路径额外范围已明确批准。核心代码变化只有pipeline.py 25行，原有五卡验收工作区文件归属保留。不把其它卡done当作前置唯一证据，也不把本地official当PAGT-007真实CI。相关用例批次存在覆盖重叠，不累加用例数量。


### 最终全量与环境失败闭环

同一指定conda解释器、同一隔离测试库与源码，显式PYTHONUTF8=1保持父/子UTF-8解码一致后，最终全量2228passed/0failed/0skipped、3条既有ast/极端向量警告，468.47秒，退出0。实际命令：python -m pytest -q -rs --basetemp=data/pytest-tmp-code-preserve-full-utf8-20261014 --junitxml=docs/reviews/code_preserve_full_utf8_20261014.xml。初次2149/79与GBK错误保留；编码修正没有修改测试、评测阈值或业务代码，定向81和整仓结果共同支持环境归因。原142与2228有覆盖重叠，不相加。Ruff/mypy和模型执行亦未切换解释器。

第1轮真实模型质量已经通过，不因本地测试编码问题追加模型请求。当前正式registry v4检查0，旧历史不变，原34既有源漂移已登记但不声明历史功能重新验收。用户批准两登记文件范围扩展已写入tasks.yaml。


### 最终关闭裁定与新鲜提交回查

最后以UTC开始时间2026-10-14T03:03:54.516727过滤新测试提交，再由独立新进程/新Session回查完整YES与短路NO的code_result，两个true，测试1passed/exit0、probe exit0。新鲜原文/命令/开始时间/probe与pipeline指纹保存在docs/reviews/code_preserve_persistence_recheck_final_20261014.json，旧239批次回查JSON保留不复用。独立confirmed_code_final_accept亲自复算真实gate、37源指纹、正式v4旧历史与check0、2228全量XML、新鲜持久化证据及原契约对账，最终验收通过。原待定全量/登记/对账缺口现闭合，AGT-003同步done；PAGT-007只更新前置有效证据，因真实GitHubCI与CI退化probe仍未执行保持in_review。用户明确业务验收委托继续有效，没有用主Agent自审替代独立身份。

本轮实际产物清单、所有命令/退出码、红绿/编码失败原证据与残余边界见docs/reviews/confirmed_code_repair_evidence_20261014.json及最终人工验收报告。真实模型只使用修后第1轮12请求，3轮预算未用满且不继续调用；本地登记不构成Git发布或生产部署。
