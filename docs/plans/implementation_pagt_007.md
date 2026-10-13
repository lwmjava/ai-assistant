# PAGT-007 Agent 基线 CI 门禁实现说明

日期：2026-10-13。状态：本地实现与验证通过，in_review；首次真实 GitHub CI 的 official 通过、gate失败；修后CI未验证，不标 done。用户批准范围 docs/、推荐方案及一次12次 official 调用，随后明确将本地模型改为 deepseek-flash。后续用户明确批准仅本卡提交、推送独立分支及一次 GitHub CI（最多12次新增模型请求，无重试）；不合并或部署。

## 实际行为与代码

维护者提交管线/工具改动后，工作流的独立 Agent job 会运行 agent-chain-v0.1 十例 official。模型凭证只在 official 步骤注入；gate/probe 使用独立模型和端点期待值，无凭证、无能力核验或护栏关闭会失败，不使用 smoke 降级。

runner 保留 --mode smoke/official 与 --report-dir，增加 --out。真实单 provider、温度0、无缓存、能力护栏开启；12次串行请求，单次60秒，总780秒。评测只选择工具，不实际调用 Tool/MCP/RAG/DB。side_effects零值仅为兼容设计声明，不能当动态计数；模型HTTP请求真实发生时不是零网络副作用。

报告包含模型、端点哈希、数据集/评分/报告/门禁版本、两阶段实际Prompt摘要、当前Git和源码指纹、完整案例和原始回复。模型身份仅为请求模型，当前字符串接口不验证服务端模型身份。限制报告1MiB、单回复16KiB；拒实际密钥回显、非法类型、不完整输出。原子排他发布，不覆盖已有证据，失败清理本次临时文件；probe输出目录不可覆盖重入。

gate 从当前生产 parser 解析原始回复，独立计数，核对输入、完整十例、独立期待身份、预测及汇总；三项阈值保持0.9/1.0/1.0。无效输入/身份exit2，指标退化exit1，通过exit0。probe独立构造route/followup/tool合法结构退化并调用真实gate CLI，必须exit1且命中目标metric reason，异常或exit2不算成功。合成探针只证明机制，不证明模型质量。

数据集冻结采用仅CRLF→LF规范的SHA256并记录算法，原Git blob摘要锁定；没有修改cases/index文件、案例数、答案或阈值。新报告不兼容历史裸字节摘要，旧报告仅E4历史参考。

本卡实际修改：.github/workflows/ci.yml；scripts/run_agent_chain_eval.py、agent_gate_check.py、agent_gate_probe.py；tests/eval/test_agent_eval_runner.py、test_agent_gate.py、test_agent_gate_entry.py；tasks.yaml；本卡docs计划/规格/Evaluation/依赖/审查/关闭对账。同期PAGT-004/005/006的app/API/config/tests改动不归本卡。

## 实际验证与失败保留

统一Python：D:\DepTooL\anaconda3\envs\ai-assistant\python.exe。pytest使用RAG_BACKEND=native、DATABASE_URL=sqlite:///./data/test_ai_assistant.db，-p no:cacheprovider，隔离basetemp，串行执行。

| 命令/入口 | 实际结果 | 证据性质 |
|---|---|---|
| 前置test_agent_chain、test_agent_chain_dataset、test_llm_route、test_tools | 71passed、0skip、exit0 | 当前前置准入，非质量 |
| 初始test_agent_eval_runner（Mock official/工具Prompt hash） | 2failed、exit1 | 有效业务红：原runner接受Mock official、SYSTEM_ACT变化无指纹变化 |
| gate实现者初版测试 | 33passed/7failed/2errors；Windows长参数ID及fitz warning断言问题 | 保留初版失败；后续修短IDs/协议行匹配，不改业务断言 |
| root合并runner/gate/一条CI/前置回归 | 137passed/2errors、exit1，49.80s | 两个超长参数ID导致Windows路径错误；非业务失败 |
| runner参数ID修正后 | 21passed、exit0，2.13s | 缺失/超时/缓存/能力/范围/原子写入正式回归 |
| CI严格YAML测试（独立验证） | 1failed/exit1，修后通过 | 真实重复键红绿闭环 |
| F06正式LF Gitblob隔离official test直接调用 | exit1、frozen_dataset_required、AssertionError | 内容不变换行导致真实入口错误拒绝；有效业务红 |
| F06修后runner+gate | 67passed、exit0，32.22s及末轮32.12s | E2/E3机制回归，不是模型质量 |
| 独立entry首次CLI | 9passed/16failed、exit1 | fitz stdout warning使全文断言错误；改匹配严格AGENT_协议行 |
| 独立entry最终CLI/workflow | 25passed、0skip、exit0，58.91s | E2实际CLI机制；报告含最终补验 |
| 独立entry最终LF补验 | 25passed、0skip、exit0，59.50s | 最终源码指纹对应的E2入口证据 |
| 独立反例审查 | 31CLI+6LF/CRLF对照=37项符合、exit0 | E2机制，源码指纹见报告及最终补验 |
| ruff check . --no-cache | exit0 | 全仓lint |
| mypy app/ --cache-dir data/mypy-pagt007 | exit0，205源文件 | 项目规定类型检查 |
| git diff --check -- runner/CI/tasks | 原runner CRLF导致exit1；仅规范LF后exit0 | 没有放宽Git whitespace配置 |

F05范围红的直接调用启动后未获得结果并被中断，不列为有效红；修复后的正式pytest/独立CLI验证拒越范围。详见修复计划和独立入口报告，不伪造每次命令通过。

实际命令详见独立报告与交付证据JSON。所有原始/中间失败保留；未执行本轮前端、全量pytest或生产测试，因为没有前端/API/运行时业务变更。本轮不是发布合格声明。

## 审查与修复

独立依赖：pagt007_dependency_check；独立计划：pagt007_plan_review（plan-eng-review）；实现gate/probe：pagt007_gate_impl；主Agent：runner/CI/整合；独立入口：pagt007_entry_validation_v2；独立反例：pagt007_counterexample_review_v2（code-review-quality）。前一审查Agent遇到用量中断未完成，不能引用为已验收。

修复F01独立计数、F02冻结及Git绑定、F03CI重复键、F04 route0.9探针选正确例、F05写入范围、F06跨平台换行。修复计划 docs/plans/plan_pagt_007_review_fixes_20261013.md。最终入口审查没有未修P1/P2，不等于整卡自动验收通过。

使用技能：用户指定task-card-delivery、brainstorming、独立plan-eng-review、独立code-review-quality。未声称调用其他技能或跨模型审核。

## 新鲜official与未验证项

本轮只读配置确认deepseek-flash、真实单provider、凭证存在、capability_version=deepseek-flash-1m-384k-20261007；未直接读取或输出.env内容。

唯一一轮已授权真实 official 完成：exit0，12次请求，route_accuracy=1.0、followup_recall=1.0、tool_selection_recall=1.0。报告 evals/reports/agent-chain-v0.1-official-pagt007-20261013.json，为本地真实模型E1；当前Silver十例结果不代表通用质量或生产成功率。随后严格gate exit0，三类派生probe总exit0；manifest记录每个实际gate子进程exit1及目标metric reason，为E2受控机制证据。没有增加模型请求。

实际命令：

```powershell
$env:RAG_BACKEND='native'
$env:LLM_CHAT_FALLBACK_CHAIN='chat'
$env:LLM_INTENT_FALLBACK_CHAIN='chat'
$env:LLM_RESPONSE_CACHE_ENABLED='false'
$env:LLM_CAPABILITY_GUARD_ENABLED='true'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' scripts/run_agent_chain_eval.py --mode official --out evals/reports/agent-chain-v0.1-official-pagt007-20261013.json
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' scripts/agent_gate_check.py --report evals/reports/agent-chain-v0.1-official-pagt007-20261013.json --expected-model deepseek-flash --expected-endpoint-hash 12b8deaccc34b32757dbb1497e029da0c2e7b26ffa86b9c926c08cb4692f4508
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' scripts/agent_gate_probe.py --report evals/reports/agent-chain-v0.1-official-pagt007-20261013.json --expected-model deepseek-flash --expected-endpoint-hash 12b8deaccc34b32757dbb1497e029da0c2e7b26ffa86b9c926c08cb4692f4508 --out-dir evals/reports/agent-chain-v0.1-synthetic-probes-pagt007-20261013
```

普通沙箱拒绝脚本换行写入，自动审批随后允许已批准六文件的CRLF→LF规范；原标准diff检查exit1、修后exit0，未放宽门禁。真实模型网络调用同样由自动审批允许用户明确授权的12请求。无自动审批拒绝遗留项。

首次真实GitHub CI已运行（run38397622273），配置实际支撑12次模型请求及三项1.0，但gate因端点摘要捕获警告污染exit2；F07本地修复后尚未重跑；required check分支保护未配置或未核验。不把本地E1或合成探针写成GitHub已通过。

自动验收必交付中的真实CI仍未验证，关闭对账不能通过；因此暂不启动最终人工审核Agent。用户未委托业务验收，不自动done。后续补真实CI、关闭对账通过后，按技能安排未参与实现/既有审查的最终审核Agent，再由用户决定业务验收。

## 非目标与回滚

未扩容十例、未重排/Query Rewrite、未改运行时Agent架构/API/数据库或生产配置、未关闭护栏/改阈值、未用Mock证实质量。无业务数据迁移；撤销独立job可恢复手工运行。已有模型/审查证据保留，不自动删除。用户新增授权后仅本卡已提交推送7bf432820c47963bd657b39da8b3987087cc8d1c；不合并部署。修订后再次CI待新增授权。

## 发布后真实 CI 与 F07修复

完整发布/失败记录见docs/plans/publication_pagt_007_20261013.md、docs/reviews/pagt007_ci_execution_20261013.json。F07正式新增反例直接执行YAML中的端点命令，在import处注入stdout警告；修前1failed/exit1，修后2passed/exit0，范围ruff0。修复将依赖import的stdout重定向stderr，只保留真实摘要stdout，保留导入失败非零及严格gate。没有再调用模型，第二次推送/CI待授权；最终独立人工验收仍不满足前置。

F07独立修后验收：pagt007_entry_validation_v2，2passed/0skip/exit0（3.27s）；实际YAML端点命令stdout仅64hex摘要、真实警告进入stderr；Git Bash完整heredoc语法exit0。范围ruff及隔离diffcheck0。旧counter审查Agent两次capacity失败，未作为修后验收证据。

同一CI另有既有后端并发测试失败：1852passed/1failed/4skipped（377.90s），tests/test_rag_039_acceptance.py::test_thread_event_loops_share_actual_process_concurrency_cap，SQLite database is locked；该测试及其运行时代码未包含在本卡发布diff，无DB操作/运行时修改，不顺手改RAG并发。Agent job为独立任务，失败已隔离记录；全工作流不满足发布通过，RAG gate被跳过。前端job全部通过，后端ruff/mypy通过。
