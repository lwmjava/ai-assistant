# PAGT-004 实现说明

日期：2026-10-13。当前候选：工作区未提交；HEAD a0d02eb411a53c3191e81f0fc99528ea60d0d3c4 不代表全部实现。原卡与用户批准范围：app/agents/、tests/、tasks.yaml、docs/、app/llm/mock.py。PAGT-006 同期未提交的服务/API/清理/互斥/文档改动保持，未归为本卡交付。

## 实际功能

默认自研 Pipeline 的复杂请求规划输出严格 Plan v1 JSON，1–16 步，含 id、description、kind、depends_on、expected_output。工具步明确 tool 与固定 arguments；依赖只能指向数组中更早已声明的步骤。整计划验证后在原行动阶段顺序执行，reason 可使用已有脱敏观测；不重新组织五阶段，不实现 DAG 或动态参数引用。

正常步骤保存 succeeded/output 与成功前缀游标。业务错误、参数错误、权限拒绝、超时、预算耗尽或计划外工具请求定位到当前声明 step_id 与公开安全码，后继仍 pending。规划失败和草稿失败以安全 error 定位 plan/具体步骤/draft。failed_step_id 专指声明步骤的 failed 记录，draft 与 preflight 不伪造成计划步骤。QualityGate 只修草稿，不重新执行成功工具。流式与非流式共用执行器，保持 stage/tool/code_result/token/error/done 事件契约。

权限仍由真实 ToolExecutor/ToolRegistry 按主体、租户、角色和风险校验；没有新增权限。工具的业务失败在原产生处分型为兼容字符串 ToolOutput；calculator/sandbox/web_fetch/file_ops/file_ops_write/web_search 的文本观察保持，Pipeline 读取结构化状态，不猜测任意错误文案。不同工具内部错误码只公开批准的安全集合，否则统一 TOOL_FAILED/PERMISSION_DENIED；审计仍记录实际拒绝理由。

v1 checkpoint 保存结构计划、每步状态、成功前缀游标，验证预算/指纹一致性；合法计划、各步完成/失败与终态持久化。恢复前缀不会重新跑已完成工具，累计预算不刷新，当前注册工具/schema 变化在任何步骤执行前完整验证。v1恢复在RAG/模型消费前从已验证结构生成唯一文本视图，避免旧文本与结构不一致。被拒绝敏感计划清空原字符串和结构字段再落安全失败终态；合成凭据反例用新 Session 读取实际state_json断言无泄漏。

旧缺字段 checkpoint 兼容文本链。`AgentPipeline(..., structured_plans_enabled=False)` 显式回滚新计划生成；已有v1快照仍按v1继续。非法新JSON不自动降级执行旧链。Mock仅在规划阶段返回有效单步reason JSON，以维持离线链路，其余阶段不改。

## 修改位置与归属

- app/agents/plan.py：严格解析/版本/依赖/数值/深度/规模/progress验证。
- app/agents/plan_runtime.py：逐步执行、状态/失败、预算/指纹、取消与只修草稿。
- app/agents/pipeline.py、state.py、prompts.py：五阶段接入、注册绑定验证、共享快照、v1 Prompt、显式legacy回滚。state.py/pipeline.py在此前PAGT-006修改之上增量修改，没有声明整个文件为本卡新增。
- app/agents/tools/outcome.py、base.py、executor.py、builtin.py、file_ops.py、web_search.py：业务结果状态兼容分型。
- app/llm/mock.py：已批准的规划阶段JSON。
- tests/test_plan_schema.py、test_plan_checkpoint_consistency.py、test_plan_tool_outcomes.py、test_structured_plan_runtime.py、test_plan_entry_acceptance.py：正式单元/隔离集成与Observed Regression。
- tests/conftest.py及旧 Agent/RAG/Chat/工具/Sandbox/Supervisor 脚本用例：逐函数 opt-in legacy_plan_mode（非autouse，不更改原业务断言），覆盖批准回滚路径。tests/test_agent_state_resume.py固定golden比较的合成时间，消除两次真实调用跨秒的环境抖动，未替换工具执行。
- docs/adr/0011-structured-sequential-agent-plan.md及目录索引、计划/修复计划、Evaluation、独立审查/入口验收、关闭对账与tasks.yaml：对应实际契约和证据。

## 验证证据与缺陷修复

解释器：D:/DepTooL/anaconda3/envs/ai-assistant/python.exe；RAG_BACKEND=native；DATABASE_URL=sqlite:///./data/test_ai_assistant.db；pytest串行、独占basetemp，无生产数据/网络/真实收费模型。证据级别：真实Pipeline/HTTP/Executor/SQLite与合成provider为E2；Schema/快照helper为E3补充；真实模型生成质量未验证。

实际命令和结果：

| 命令（均通过声明Python -m） | 退出码与结果 |
|---|---|
| pytest tests/test_structured_plan_runtime.py -k 'credentials or revalidates or preflight_and_quality or invalid_draft' -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt004-review-red | 1；9 failed/16 deselected，2.92s，实际泄漏、错误恢复调用、无runtime timeout、非法draft原始错误；有效先红 |
| pytest tests/test_plan_checkpoint_consistency.py | Agent先红9failed/3passed；修复12passed，退出0 |
| pytest tests/test_plan_tool_outcomes.py | CE06正式5先红；修复19passed，退出0，2.32s |
| pytest tests/test_plan_schema.py tests/test_plan_checkpoint_consistency.py tests/test_plan_tool_outcomes.py tests/test_structured_plan_runtime.py tests/test_plan_entry_acceptance.py tests/eval/test_rag006_pipeline.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt004-review-green-2 | 0；158passed，6.64s（后续追加真实file_ops Pipeline正式例） |
| pytest tests/test_structured_plan_runtime.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt004-review-ce06-pipeline | 0；27passed，2.08s |
| pytest -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt004-full-20261013 | 1；14failed/1961passed，405.38s。13为旧plaintext provider缺显式legacy模式，1为datetime黄金比较跨秒；保留并修复，不算最终全量通过 |
| pytest tests/test_agent_state_resume.py tests/test_rag_029_pipeline_alignment.py tests/test_rag_037_retrieval_status.py tests/test_sandbox.py tests/test_supervisor.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt004-full-fixes | 0；95passed，5.77s；原业务断言保留 |
| pytest tests/test_structured_plan_runtime.py tests/test_plan_entry_acceptance.py tests/test_plan_checkpoint_consistency.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt004-ce07-green | 0；53passed，3.94s；随后新增流式参数独立2passed |
| ruff check --no-cache . | 0；All checks passed（最后业务改动后重验通过） |
| mypy app/ --cache-dir=data/mypy-pagt004-final | 0；204 source files（最后业务改动后重验通过） |

独立计划审查与依赖复核：/root/pagt004_plan_review / /root/pagt004_dependency_check；前置工具专项171passed当前证据。实现子Agent：plan_model负责模型/快照、tool_outcomes负责工具结果；root整合Pipeline与文档。独立验证 /root/pagt004_entry_validation，13真实HTTP/ChatService/Executor/SQLite入口E2均通过。独立入口反例发现2P1/4P2，CE01-07按修复计划正式先红后修复；原审查Agent复验时usage limit退出，不能把原报告当修复通过。另启用独立repair_recheck进行实际复验，CE01-07均通过，无未决P1/P2；独立focused72passed与同步/流式canonical恢复2passed，见最终repair-recheck报告。工程自动门禁通过，最终独立人工审核第1轮已通过，356passed/1622deselected/21.03s/退出0，37文件SHA全部匹配；报告docs/reviews/2026-10-13-PAGT-004-manual-review-1.md。用户业务验收未委托，任务保持in_review未关闭。

实际读取使用技能：task-card-delivery v1.3.0、brainstorming、plan-eng-review；独立审查采用code-review-quality要求的等价上下文/入口反例审查。不能把仅有技能名称当运行证据。

最终全量命令：`python -m pytest -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt004-full-final-20261013 --junitxml=data/pagt004-full-final-20261013.xml`，退出0。3warnings为sandbox ast.NameConstant弃用及既有数值反例numpy divide/overflow，不是跳过测试。

## 未做项、限制与回滚

没有通用DAG、并行步骤、动态工具参数引用、前端步骤UI、API配置契约或数据库schema迁移，没有真实模型规划遵循率/外部服务可用性/性能测评。未跑前端检查或页面验收，本卡不改变前端。SQLite/E2不能证明PostgreSQL/多实例/exactly-once；工具副作用后checkpoint写失败或断电窗口仍沿用既有PAGT-006限制。checkpoint写失败为既有fail-open，不能保证故障窗口精确恢复；必须人工核查外部副作用，不自动盲目重试。

写入次数为计划合法生成1次、每步成功或失败边界最多16次、最终终态1次，正常完整请求最多18次；取消保存最后完成前缀，QualityGate重入只修草稿不重复步边界。工具重试遵既有受控契约，不宣称额外exactly-once。

回滚先用structured_plans_enabled=False，仅停止新v1生成，保留旧链和已保存v1恢复。全代码退回不理解v1的旧版本前，停止并人工核查活动v1请求，不能盲目重跑敏感副作用。无提交、推送、合并、部署、重要数据删除或迁移。

最终全量1978passed/3warnings，428.10s，退出0；ruff --no-cache全仓与mypy app/204source退出0，diff --check退出0；JUnit data/pagt004-full-final-20261013.xml。独立复验完成；人工审核与最终指纹结果见关闭对账记录及manual-review-1报告；用户未委托人工业务验收，自动化全通过也保持in_review交用户决策。

最终人工审核Agent在报告落盘、核对结果已通知root后，生成最终回复时出现model capacity错误；root实际读取报告确认已包含完整身份、命令/结果、指纹和工程通过结论。该最后回复错误不冒充审核缺失，也不隐瞒执行故障。最终用户业务验收仍未验证。


## 2026-10-14 已授权业务验收交付补记

用户本轮明确委托独立人工验收Agent代行可复现业务操作。结构化Plan全计划预检、逐步执行/失败定位、取消恢复、预算、权限和旧链回滚均在当前入口验证通过；398专项与最终8独立探针退出0。逐项原契约E2业务结果及当前指纹见[独立验收报告](../reviews/2026-10-14-PAGT-004-人工验收第1轮.md)；报告的相关回归/独立探针存在覆盖重叠，不累加为独立用例总数。原“用户未委托/未验证”是此前时点记录，本轮授权和实际验证已解除该项，本卡状态同步为done。

本轮仅新增验收文档/隔离探针并同步任务和交付记录，业务源码未变；原批准范围及功能回滚方式不变，真实LLM质量、生产PostgreSQL、多实例、生产性能及断电exactly-once仍没有本轮验证，不升级为生产可用或整体上线通过。无收费模型、CI触发、提交、推送、合并、部署或重要数据操作。
