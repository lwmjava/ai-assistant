# Agent 空响应整改实现说明

关联 QA-005；计划 plan_qa_005_agent_empty_response_20261014.md。基线提交7b280df0f59523069fdeedfbd7c9428c42159ecb，开工工作区干净；候选为HEAD加机器证据列出的423个最终输入指纹，尚未提交。Windows11 / Python3.12.0，指定conda解释器 D:\DepTooL\anaconda3\envs\ai-assistant\python.exe；依赖与非敏感配置见机器报告。

## 归因与实际行为

[原GitHub运行](https://github.com/lwmjava/ai-assistant/actions/runs/38548192922) 的Backend和Frontend通过，Agent job115799387361失败。原报告agent-003的tool_reply为空，工具名和有效调用召回均0.5，其余路由指标1.0；fitz弃用警告不是退出1原因。模型deepseek-flash，endpoint_hash=12b8deaccc34b32757dbb1497e029da0c2e7b26ffa86b9c926c08cb4692f4508。原artifact SHA256=43d40bef93c16ae80ee10fe9da37c7de5ac2acde85d526817bda98410f1a9b6d；原完整报告保留在docs/reviews/agent_empty_original_20261014.json。

同模型/端点的单次获准诊断返回HTTP200、stop、非空内容，无原生工具字段；证据docs/reviews/agent_empty_diagnostic_20261014.json。历史报告没有finish_reason，无法断言过去是截断、原生工具还是供应商瞬时空结果；能确认的代码缺口是提供商把空HTTP成功结果当成功字符串，评测无恢复/结束原因记录。和前次成功报告相比，关键Prompt/提供商/评测代码未变，不归咎于最近身份fixture或Linux沙箱修复。

普通非流式调用现在对空串、空白、null抛固定llm_empty_response；无效结构抛llm_invalid_response，有效文本原样返回。安全元数据仅结束原因白名单/原生工具存在布尔；不记录供应商正文/推理或凭据，不加入共享last-response状态，不修改流式路径、输出预算、权限、缓存作用域或提供商重试/failover。

正式评测仍12个逻辑首请求，整轮最多1次符合stop/missing且无原生字段的空恢复。补发同模型、提示和选项，非空错误答案不补发；每个逻辑调用60秒含恢复，全轮780秒不重置。截断、内容过滤、旧function_call、畸形非空tool_calls、无效结构与HTTP错误均不恢复。所有尝试保存在完整报告；最终空响应使门禁失败。中断保存独立*.attempts.json失败账本，不作为official报告；完整报告或失败账本已存在时在调用前拒绝。

门禁独立核对12～13次请求、恢复资格、顺序和最终raw绑定，重新计算原四指标；冻结Silver案例/期望值/Prompt和0.9/1.0/1.0/1.0阈值保持。故障探针同步最后尝试文本，保留初始空尝试，仍须以目标质量指标exit1而非身份错误exit2失败。详见docs/evaluations/qa005-agent-empty-response-v1.md。

## 文件与验证

- app/llm/openai_compatible.py：响应边界及固定错误；tests/test_llm_response_validity.py：隔离HTTP红绿/元数据/正常兼容。
- scripts/run_agent_chain_eval.py、agent_gate_check.py、agent_gate_probe.py：一次恢复、所有尝试/失败账本、独立绑定和诊断。
- tests/eval/test_agent_eval_runner.py、test_agent_gate.py、test_agent_gate_entry.py、test_agent_recording_tools.py：恢复资格、全局预算、同输入、超时、已有账本0调用、造假拒绝；更新fixture保持原指标拒绝断言。
- docs/ai-prompts/登记：追加prompt-model-v7，v1-v6对象未改，默认模型指纹不变；docs/plans/、docs/evaluations/、docs/reviews/及tasks.yaml同步。

开工全量2292passed/0failed/0skipped，568.98秒，exit0。HTTP边界先红15failed/1passed，exit1；初版专项119passed/6failed来自旧fixture未绑定尝试，补齐后125passed/exit0。独审补修后提供商+runner专项51passed/exit0。两轮被中止的全量不作通过：第一轮因独审账本前置检查修正，第二轮发现recording-tools旧fixture绑定遗漏；该专项红1failed/32passed，修正仅新增对应尝试文本绑定，metric_integrity断言保持。

最终版本完整全量2329passed/0failed/0error/0skipped，548.45秒，3项既有warning，exit0；报告docs/reviews/agent_empty_final_20261014.xml，最终Evaluation专项338passed/exit0。Ruff --no-cache、mypy app/（205源）、pip check、registry check与diff check退出0。首次Ruff因原.ruff_cache临时文件权限失败，使用--no-cache完成同规则检查；不作为业务失败或降低规则。

唯一真实完整轮 evals/reports/agent-empty-official-20261014.json：四指标1.0，model_requests12，empty_response_retries0，official退出0；独立gate退出0，四类故障probe退出0（每个派生故障gate均exit1目标指标）。连同1诊断实际13次，低于14次上限，不再执行额外诊断或第二轮。不用合成结果声称模型质量提升，本轮也未在真实请求中触发恢复；恢复能力由确定性契约证明。

实际完整命令、退出码、报告路径与最终输入清单在docs/reviews/agent_empty_validation_20261014.json。pytest使用隔离data/*test_ai_assistant.db、独立basetemp、UTF8和空进程模型/Embedding密钥覆盖，CI启用的langgraph extra本地已安装；版本不同由机器证据说明，不能冒称完全同环境。独审3项P2闭合，无剩余实质P1/P2，详见agent_empty_review_20261014.md。

最终423项源码/测试/配置/依赖/评测输入规范化SHA256清单复核一致，汇总c47a446e60790e77be53f3edc8cc2b868bb7c22ef9fbc6d62562a07f3971d150；此后仅补充任务/验收说明，不改变检查输入。Linux CPython3.12.7在已有WSL运行时执行实际报告函数AST抽取，原子创建、不覆盖及清理、失败清理、越界拒绝4项通过，exit0；绑定runner原字节SHA256=151230d13add689e9273140485774f9db1fa901fbab5a759d517ebf22b9023f9。仅证明文件落盘路径，不替代Ubuntu完整CI。

## 待验收与回滚

纯后端响应/评测改动无前端契约影响，本轮前端检查不适用；原提交Frontend通过属于历史事实。最终新提交的Ubuntu Backend、Frontend及Agent official CI尚未执行，人工业务验收待完成，QA-005保持in_review。此次不新增平台进程分支；旧Linux沙箱检查不能绑定本轮新提交。未修改CI、冻结数据集、.env或重要数据，没有自动提交、推送、部署或更改远程分支保护。

回滚仅恢复匹配旧源/脚本/测试和版本登记，保留所有失败/通过报告，不只切current指针；无数据库迁移。剩余运行风险：提供商仍可能返回非空错误答案、截断或空响应超过一次，此时门禁继续失败；本轮不是生产质量保证。
