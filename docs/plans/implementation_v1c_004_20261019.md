# Supervisor 受控工具委派实现说明

2026-10-19。任务 V1C-004，基线 e005b4d08b5139080423f780db8c60f92d5d219e，分支 codex/v1c-001-lazy-retrieval；候选为 HEAD 加[526项最终输入摘要](../reviews/2026-10-19-v1c-004-evidence.json)，尚未提交。用户批准实施计划、允许路径与 ADR-0015，随后明确批准仅追加预算提示测试断言。当前本地自动门禁已通过，任务in_review；独立最终审核、用户业务接受及最终候选CI待完成。

## 谁可以做什么

沿用现有身份验证与编排开关。进入 Supervisor 的请求，research 节点现在可把工具行动委派给现有 AgentPipeline 行动循环，并经同一个 ToolExecutor 执行。控制和 draft 节点仍没有执行工具的权力。默认 self、快路径优先级和可选 LangGraph 依赖不变。

新委派只允许注册且有有效契约的 L0/L1 工具：审计必须启用、版本/权限声明完整，超时为非bool有限正数且不超过300秒，重试为非bool整数0..3；L1必须要求认证。未知、未治理 MCP、无契约与L2/L3能力拒绝。原角色、租户、参数与工具内部资源检查继续生效，技能绑定只收窄允许集，不授予权限。生产写入与真实模型费用未获本卡授权。

有委派能力的 Supervisor research 支持工具模式技能，仍执行有效版本和私有隔离校验；其他简单/知识库快路径不由本卡升级工具组合能力。ADR-0014 的后续补记与 README 支持矩阵描述该新增范围，能力矩阵仍按实际部分实现说明。

## 成功、失败和中断行为

- 真实工具结果用于后续行动和草稿；用户可见结果由代码确定性前置工具执行事实。ok、denied、invalid、error、timeout、not_called、skipped、budget_exhausted与cancelled分别表达执行类别，不用模型自述当作调用凭证。
- 同一请求共享 AgentState、工具尝试轮次、已执行指纹和观测。跨研究轮次不重置预算，拒绝/无效提议也占尝试额度；达到额度后不反复生成行动收尾。相同参数不重复执行，新请求不承诺跨消息exactly-once。
- Service 在每次新尝试前用独立 Session 核验用户/租户/角色/token版本；没有技能也核验。工具重试每次底层启动前同样复核主体和技能。无法确认授权时结束，不启动后续模型或工具；执行身份取已验证的请求主体，执行后还原 ContextVar。
- 流式 tool.call 先交给消费者，等待其恢复迭代后再复核并启动工具。取消传到生产任务和执行器；关闭外层迭代器显式关闭内层并收集生产任务，避免等待确认的任务泄漏。执行中及重试退避中的取消有审计，不标记为成功，也不承诺撤回已发生动作。
- 已成功工具后发生模型异常或后续失败，已有结果在 finally 中采集，异常出口仍展示执行事实；审计和已发生副作用保留。新路径的工具异常原文不放入公开观测。
- 工具正文作为不可信资料，模型输入有围栏、脱敏和6000字符上限；控制节点只接收安全状态摘要。执行护栏检查真实返回正文，排除系统生成的工具名/参数包装，避免误拒同一工具的合法不同参数。原始用户目标保留作授权依据。
- 没有委派器的旧构造继续不执行工具，并确定性显示not_called；预算错误保留完整“请缩短输入”文案且不诱导稍后重试。Supervisor显式checkpoint恢复仍拒绝，不新增恢复表或自动重放。

## 实际代码位置

| 文件 | 实际职责 |
|---|---|
| app/services/supervisor_tools.py | Service主体复核回调、委派装配与权威执行身份 |
| app/services/chat_service.py | 普通/流式Supervisor装配、嵌套事件迭代器回收及类型收窄 |
| app/agents/supervisor_tools.py | 有效契约允许集、共享预算/观测、拒绝审计、确定性摘要与正文防护 |
| app/agents/supervisor.py | research委派、控制/草稿边界、tool.call确认关口、最终披露和异常结果 |
| app/agents/pipeline.py | 可选累计预算与授权钩子，既有行动循环复用，原路径默认兼容 |
| app/agents/tools/executor.py | 可选逐attempt复核与新路径异常脱敏，取消审计覆盖逻辑调用及退避 |
| tests/test_supervisor_tool_contract.py | 主线34条普通/流式、预算、状态、安全及取消反例 |
| tests/test_supervisor_tool_contract_review.py | 独立49条契约、主体/技能、缓存、兼容及回收反例 |
| tests/test_supervisor.py、test_tool_path_contract.py、test_skill_path_contract.py | 新事实披露、取消审计及新增技能支持的旧调用方断言更新，原拒绝断言保留 |
| tests/test_rag_028_model_capability.py | 获用户追加批准，只调整预算提示回答断言：完整提示后缀+not_called，保留禁止重试文案 |

README、能力矩阵、ADR-0014补记、ADR目录、任务卡和版本化Evaluation同时更新。没有修改API/schema、前端、依赖、CI、数据库迁移、路由或默认安全配置。

## 已执行验证与版本边界

解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe；当前测试使用 DATABASE_URL=sqlite:///./data/v1c004-regression-test.db，所有输入与依赖版本详见 evidence.json。conftest清除真实模型凭据并阻断非本机网络，工具和数据均为隔离合成输入。

| 检查 | 实际结果与退出码 |
|---|---|
| 开发前相关基线 | 171 passed /1 warning，37.43秒，0；Ruff/mypy 0（214文件） |
| 初始主线红测试 | 10 failed，6.84秒，1；真实函数零调用等业务失败 |
| 审查反例 red2/red3 | 各5 failed，4.21/4.33秒，1；后续均有修复复验 |
| 最终相关十文件回归 | 286 passed /1 warning，76.99秒，0；日志 contract-final2.txt |
| 追加批准预算提示整文件 | 44 passed，3.21秒，0；日志 budget-approved.txt |
| 独立当前入口复验 | 49 passed，20.86秒，0，无警告；日志 independent-final4.txt |
| Ruff全库无缓存 | 0；日志 ruff-final.txt |
| mypy app/ | 0，216文件；日志 mypy-final.txt |
| 最终无筛选全量 | 2882 passed /3 skipped /3 warnings，688.05秒，退出0；526项输入一致 |

完整命令及输入SHA256见[机器证据](../reviews/2026-10-19-v1c-004-evidence.json)。最终全量命令为 `python -m pytest --basetemp data/pytest-tmp/v1c004-full-final2 -q --tb=short`，使用上述指定解释器和隔离库。结果已按最终输入记录；相关输入变化后重验。

Cursor 接续阶段由用户提供的全量报告为2881 passed/1 failed/3 skipped，752.19秒，1，唯一旧预算逐字相等断言已按明确追加授权调整；该报告作为明确来源的历史失败，不冒充主Agent本轮全量。独立green3的49通过也对应旧Supervisor/Service指纹，不直接用于当前候选；已由final4重新实跑。

E2证据来自真实Service/LangGraph/SQLite与本地工具，证明权限、接线、执行事实和生命周期；合成模型不证明生产模型质量、检索质量或性能。数值helper断言只作E3补充。Evaluation supervisor-tool-contract-v1.yaml 1.0.0为AI-authored Smoke/Adversarial，非Gold。无前端/API变更，前端四门和新增页面操作不适用，不宣称页面实测。

## 独立审查、分工和未做项

主Agent负责Service、主线测试、接续核对、总门禁和文档；/root/v1c004_runtime负责Agent/Executor实现；/root/v1c004_validation独立构造并实跑入口反例；/root/v1c004_engineering独立方案和代码静态审查，不冒充自己运行pytest。Cursor在中断期间接续了类型收窄、文档与回归，用户明确交接，当前改动予以保留。已发现P1/P2、红/绿及测试夹具纠正完整保留在review-fixes和两份独立报告。实际使用task-card-delivery与brainstorming；工程按架构/兼容/权限/失败/测试/性能等价维度审查，没有宣称运行完整交互式plan-eng-review。

模型缓存反例使用真实OpenAI兼容provider、MockTransport与真实BoundedCache。普通请求证明工具提议缓存命中后新请求仍执行工具并新增审计；流式只证明实际模型缓存命中与新执行，不声称行动提议命中或100%命中。后台producer继承的临时cache lease失效时安全旁路缓存是既有边界，本卡未优化。

不做V1C-005资料参数提取与组合规划、并行工具、新执行器/框架、未治理MCP、高风险自动写、Supervisor持久化恢复、跨实例租约或跨消息exactly-once。既有重新生成等时间戳排序缺陷仍未修复；V1C-003关闭证据不代表该缺陷消失。

## 回滚与任务状态

回退Service/研究委派接线和新增可选累计预算/执行钩子，Supervisor恢复无工具并明确not_called；保留消息、审计及旧checkpoint，不重放已发生动作，不做数据迁移。默认self保持。全量通过后才启动未参与本卡实现和既有审查的新最终审核Agent；本卡业务接受未委托、最终候选CI未取得，未齐时保持未完成。尚未提交、推送、合并或部署，不沿用前卡Git或业务接受授权。

Final verification update: unfiltered full pytest passed on the frozen526-input candidate (2882 passed /3 skipped /3 warnings,688.05s,exit0). The three warnings are existing ast.NameConstant deprecation and the RAG numeric-adversarial divide/overflow warnings. Windows skipped CLI symlink capability, POSIX0600 permissions and open-file replacement identity; final-candidate Linux CI remains pending. Raw logs are archived in docs/reviews/2026-10-19-v1c-004-log-*.txt with SHA256 in evidence.json. Only documentation changed after verification.

Final independent technical audit passed: /root/v1c004_final_audit ran184 tests (50.89s,exit0,one existing warning), independently matched all526 input hashes and23 archived logs, and found no remaining in-scope P1/P2. Report: [independent final audit](../reviews/2026-10-19-v1c-004-人工审核-第1轮.md). Current status in_review; only user business acceptance and final-candidate CI remain. Earlier pending-final-audit statements describe the prior workflow stage. No commit/push/merge/deployment performed.

## Delegated business acceptance authorization

2026-10-19: the user explicitly authorized an independent Agent to perform V1C-004 business acceptance covering actual tool execution, not-called/failure feedback, permission and skill restrictions, cross-round budgets, cancellation and audit, and to record the conclusion after it passes. A new independent identity /root/v1c004_business_acceptance is assigned. This supersedes the earlier lack of delegation but is not a predeclared pass. No commit/push/deployment is authorized by this request. The final candidate CI gate remains pending.

## User-delegated business acceptance result

2026-10-19: new independent Agent /root/v1c004_business_acceptance passed the explicitly delegated business acceptance. It independently read original requirements and real user-result assertions, ran178 tests (59.80s,exit0,one existing warning), and verified all526 frozen input hashes before and after. Report: [business acceptance](../reviews/2026-10-19-v1c-004-business-acceptance.md). This covers real execution, not-called/failure disclosure, permission/skill boundaries, shared budgets, cancellation and audit using E2 isolated real code and local tools with synthetic models. No production model-quality claim. The only remaining delivery gate is CI for the final candidate commit. Status stays in_review; no commit/push/deployment is authorized or performed. Earlier pending business acceptance statements describe the prior state. Only documentation changed after verification.


## 最终关闭补记（2026-10-19）

用户已明确要求关闭V1C-004。最终业务提交9cbe94fffe269cde9d93c00fb6d1f6d25fd01264的CI run39268207253，Backend/Frontend/Agent official baseline全部success，官方四项指标均1.0，Prompt指纹与本地一致；本轮新独立审核120 passed/37.64秒/exit0，526项当前输入匹配。任务现为done；前文未提交/未推送/待CI语句保留为历史记录，不代表当前关闭状态。完整对账见docs/reviews/2026-10-19-v1c-004-closure.md及ci-protocol-evidence.json。本轮仅修改文档与任务状态，没有改变已验证执行输入，没有合并或部署。
