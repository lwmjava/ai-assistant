# Supervisor 受控委派：独立工程审查

日期：2026-10-19。独立身份：`/root/v1c004_engineering`。阶段：实施前方案审查及首版代码审查。初审时 ADR-0015 为 Proposed；后续用户明确批准后已改 Accepted。本文不授予发布或业务验收权限。

## 依据与方法

读取 AGENTS.md、task-card-delivery SKILL.md、迭代指导、Harness 治理、适用 00/01/02/03/04/07/08 规则、tasks.yaml 原卡、ADR-0010/0014、真实 Supervisor/ChatService/Pipeline/ToolExecutor/Registry/SkillRuntime/缓存代码，以及本轮 plan_v1c_004_20261019.md、ADR-0015 和 baseline.json。

本 Agent 未参与主要实现，未修改业务或测试，未运行 pytest，避免与主线共享测试库并发。基线 171 passed/1 warning、Ruff/mypy 退出 0 为主线实际记录，非本人执行；前置全量和 CI 仅作历史证据。按照架构、权限、失败、测试、性能和范围六个维度执行等价工程审查，未调用或宣称执行完整交互式 gstack 评审。

## 结论

推荐研究节点内委派既有行动循环，保留 Supervisor 控制和草稿，不调用完整 Pipeline.run。该方向满足原卡受控执行或显式委派、不新建执行器、不任意并行的边界。下述两处澄清已独立复核补准，**当前方案可交用户批准**；ADR 仍为 Proposed，批准前禁止修改业务代码。此结论不是实现审查通过或任务验收通过。

1. **有效工具契约的可执行定义**：仅检查 contract 非空和 risk 不足。新路径应拒绝非有限或非正 timeout、bool/负值/无界 retry、无审计等不满足治理的契约。建议明确有限 retry 上限，且不放宽既有契约策略。现有 ToolContract 为 dataclass，没有这些运行时验证；保护限定本卡新路径，避免全局改变旧工具行为。
2. **与组合任务的边界**：当前 Supervisor 已先检索，保留经预算的上下文不等于本卡完成知识库与工具组合。计划应明确不新增组合规划、意图路由或私有资料参数提取保证；V1C-005 原卡仍保留，现有检索材料的不可信工具防护必须继续生效。

以上为方案澄清，无需改换推荐架构。最终只读复核确认：计划步骤 9 与 ADR-0015 第 11 点已定义 L0/L1、audit=true、版本/权限完整、非 bool 有限 0<timeout≤300 秒、非 bool 整数 retry 0..3，失败关闭且只限制新增路径；计划单次检索段落和 ADR 第 12 点已明确不新增组合规划、资料参数提取或路由。计划预算段及 ADR 第 13 点进一步约束 R 为非 bool 整数 0..10、T 为非 bool 整数 0..100，非法值拒绝而非截断，直接构造 T=0 不执行。**两处建议均已补准，无剩余方案阻断，可提交用户批准**。尚无实现代码可判定剩余 P1/P2；不能从方案完善推导实现无缺陷。本轮补记只改本文，未运行测试，未修改业务或测试文件。

## 六维检查

| 维度 | 核对结论与实现门禁 |
|---|---|
| 架构 | Agent 不读数据库；Service 注入主体复核；统一 Executor；共享请求状态。新增独立 tools 节点易重复解析和预算，完整 Pipeline 委派会重复检索、规划及回答，两者不推荐。显式恢复继续拒绝，不伪造 state_store。 |
| 权限 | 每次实际启动前 fresh User/Tenant/role/token_version，空技能同样执行；只允许具有效契约的 L0/L1，与技能名单取交集；L2/L3、无契约、未知名称拒绝。ADR 明确补充原 0014 的工具模式限制。工具观察和模型研究文字不能扩大授权。 |
| 失败 | typed 实际结果区分成功、拒绝、参数非法、错误、超时；未调用、重复、预算及取消另作摘要分类，避免传入 ToolOutput 不支持的枚举。最终确定性披露，模型自称成功不能覆盖事实。取消停止后续工具/草稿，不承诺回滚已发生动作。提前拒绝和取消核对审计与副作用。 |
| 测试 | 必须真实 ChatService 普通/流式、真实 LangGraph、隔离库新 Session 回读；覆盖两轮预算、重复、无调用伪成功、部分成功后失败、角色/租户/技能撤销、恶意资料、缓存命中仍执行、无 delegate 及旧 __new__ fixture。独立入口审查及全新最终审核仍未执行。 |
| 性能 | T 工具尝试、R 研究轮共享累计，拒绝和重复也消耗额度；已耗尽不反复收尾。计划保守模型调用界 T+3R+2 合理，须按用途实际计数验证，Service 既有前置用途另计。新增资料有界；逐调用超时；不逐 token 查库，不新增真实费用。 |
| 范围 | 允许清单覆盖业务接线、旧相关测试、独立新测试、Evaluation、ADR及文档。尚待用户明确批准。API/schema、前端、依赖、CI、生产数据、RAG组合、并行、高风险、持久化恢复不在本卡。回滚恢复无工具模式并保留历史，不重放动作。 |

## 已识别的实现陷阱

- Pipeline 无 store 会在每次行动循环把 tool_rounds_used 重置为 0；必须显式累计模式，不能仅复用实例。
- Registry 当前允许 contract=None；新委派路径不能把注册等同治理许可。
- SkillRuntime.check 在无版本时不查身份；不能替代通用主体复核。
- 每次耗尽仍调用 _finalize_without_tools 会增加重复模型调用，调度需直接终止行动。
- research/draft 原摘要全部来自模型，subtask 原 status=done；保留兼容事件枚举时仍必须在摘要正文呈现真实执行分类。
- 缓存为模型层缓存；不得缓存或复用真实工具完成结论。新消息不承诺跨消息 exactly-once。
- 原始用户目标与 retrieval_query 保持；禁止用模型研究文字代替授权依据。工具和研究资料在 worker 中围栏、有界，在控制侧仅传安全状态摘要。

## 后续门禁

用户批准具体 ADR/方案/路径后，先红测试再最小实现；本 Agent 可继续独立工程复核，但不得兼任最终全新审核 Agent。最终完整 pytest、Ruff、mypy、必要平台证据、原始交付 E1/E2 对账、独立入口审查、最终审核及业务接受须分别记录；未满足不得 done。无本卡 Git 授权，不自动提交或推送。

## 首版代码独立审查（批准后）

本轮只读完整工作区相关业务 diff，额外读取未跟踪的 app/agents/supervisor_tools.py 与 app/services/supervisor_tools.py、工具执行器、Prompt、既有不可信拒绝 helper 及正式新增测试。未运行 pytest；主线 10 项通过与独立 Agent 正在运行的 43 例不作为本人实跑。本轮仅写本报告，发现交主 Agent 记录修复计划并补红测试，不自行修复业务。

已有合理接线：统一 Executor、显式累计预算、请求内原始 AgentState、L0/L1 契约资格筛选、空技能仍 fresh 主体核验、流式 tool.call 确认后再 guard、控制节点仅状态摘要、默认无 delegate 可选兼容。以下发现未闭环，不能验收。

| 编号 | 严重度 | 发现、位置及实际影响 | 建议正式反例 |
|---|---|---|---|
| E-01 | P2 | delegate.iter_research 仅正常退出循环后采集实际 ToolOutput；成功工具之后下一模型/主体异常会跳过采集。Supervisor.run 异常出口也不附真实摘要，最终只有通用失败文字，丢失部分成功及已发生动作说明。 | 首工具成功后第二次模型抛错或撤销主体；普通/流式最终可见结果仍明确已有 ok，不能表示从未执行。 |
| E-02 | P2 | ToolExecutor 的取消捕获只覆盖单次工具函数 await；重试退避 sleep 位于捕获之外。退避期间取消，没有此次逻辑调用聚合审计。 | 幂等工具第一次瞬时失败后退避期间取消；新 Session 必须读到一次取消审计、func 不再执行。 |
| E-03 | P2 | Service wire_supervisor_tools 对空注册表直接 return；真实请求落为旧无 delegate 模式，没有确定性 not_called 披露。直接构造兼容同样仅依赖模型遵守“没有工具”Prompt，模型可在最终文字伪称成功。 | 空注册表真实普通/流式，模型反复声称已完成；仍确定性告知工具未调用，零副作用。旧构造同样不得伪称执行。 |
| E-04 | P1 | 新增 UNTRUSTED_TOOL_RESULTS 围栏未接执行防护；SYSTEM_ACT 仅声明 UNTRUSTED_SOURCE，_reject_untrusted_tool 仅查 state.context，不查 tool_results。恶意工具结果点名另一个允许的工具，模型服从后仍可执行，工具资料被升级为新动作依据。 | 工具 A 返回调用 B 的指令，原用户未点名 B，模型紧接提议 B；B 零副作用且真实 denied/审计可见。不仅检查 Prompt 文本。 |
| E-05 | P1 | 主体/技能 guard 仅在逻辑执行器调用前执行。首次瞬时失败后重试没有再次复核；退避时撤权或技能变版，第二次 tool.execute 仍会启动，违反每次新启动前复核。 | 第一次瞬时失败时独立 Session 撤销主体/技能；后续 retry func 零新增调用，并明确拒绝审计。可选 before_attempt 回调只注入本卡路径，保留旧默认兼容。 |

这些结论来自静态可达调用链，尚待正式红反例运行验证；不能将未实跑描述成复现通过。另有文档漂移：计划文首仍写等待批准/业务未修改，虽然 ADR 已 Accepted 且首版代码存在；建议主 Agent 更新当前状态并保留历史批准记录。

## 修复后最终源码只读复核

主 Agent 要求恢复后先盘点再继续，本 Agent 重新读取当前六个业务文件、完整关键 diff、正式红例测试、review-fixes 与独立入口报告；仍不运行 pytest。初审的静态结论已由主线正式红测试复现：review-red2 五失败、review-red3 五失败，后续主线 34 passed（9.52 秒）与独立 green3 49 passed（19.52 秒）。这些命令为各执行 Agent 的实际记录，非本 Agent 实跑。

| 问题 | 当前源码修复核对 | 本轮状态 |
|---|---|---|
| E-01 部分成功丢失 | iter_research 在 finally 增量采集已完成 ToolOutput；Supervisor 异常出口也前置实际 summary。已有成功结果不会因后续模型错误丢失。 | 静态修复确认；正式普通/流式红后绿见主线34例。 |
| E-02 退避取消审计 | Executor 外层 try 包住完整 while、实际工具与 sleep；CancelledError/GeneratorExit 统一聚合取消审计再抛出。 | 静态修复确认；退避取消正式红后绿。 |
| E-03 未调用披露 | _tool_summary 对无 delegate 固定返回 not_called；正常及异常最终回答均确定性附加。Service 空注册表依然不注入，但最终可见披露不再依赖模型。 | 静态修复确认；旧直接构造与空Service正式用例已绿。 |
| E-04 工具输出注入 | 累计模式创建 guard_state 副本，仅取 ToolOutput 格式化观测中受信 ` 结果=` 分隔后的外部正文，标记 UNTRUSTED_SOURCE 后复用既有拒绝规则。不会把工具名包装误当恶意点名；原始用户目标不变；拒绝有决策审计。 | 静态修复确认；A点名B拒绝和合法同工具不同参数继续均有正式绿例。该防护仍是既有点名规则，不声称解决所有语义注入。 |
| E-05 重试前授权 | 新委派注入可选 before_attempt，每次 func 启动前同步 fresh actor 与 skill.check；异常先审计再传播。默认旧执行器未注入回调，不改变旧调用方。 | 静态修复确认；主体撤销及技能变版阻止第二次启动的正式用例已绿。 |
| E-06 嵌套流清理 | ChatService finally 显式 await 内层 events.aclose；Supervisor finally 取消并 await producer，ack 不会在外层 close 后继续放行。 | 静态修复确认；独立入口关闭反例已绿。 |

本轮未发现新的本卡 P1/P2 业务缺陷。此结论是当前源码的独立工程复核，不构成完整技术验收或用户业务接受。

当前基线 HEAD 为 e005b4d08b5139080423f780db8c60f92d5d219e，工作区未提交。本轮读取指纹：

| 文件 | SHA256 |
|---|---|
| app/agents/supervisor_tools.py | af64caf61fa23fed526ee07e7ce16e0649fd68d078826be02bf1eee6c57fe44e |
| app/services/supervisor_tools.py | 12916f8eeb28f1faf9bbfa8d3c5c5e27e37b6d7cd8e75fcab8ae014b2d5fc60d |
| app/agents/pipeline.py | b1cfcef264d32dc86ca1b1e1d07e63ae7e89bc2c81bba1e1d3a3d5bc7d236fc5 |
| app/agents/supervisor.py | d8ca7b41df1d5a33abf9700f0cdd56986bf382b7a239f4cb86fcdd4e9473f71d |
| app/agents/tools/executor.py | 536bb68cb75c4ec96d5c748926113efdd25c31975bd94abaef155bfd53d44a59 |
| app/services/chat_service.py | 19b856eba077595bf56e7a4a1224156a493fee235d1a1c1e0159b6524cffa4a0 |

独立 green3 输入清单中 Supervisor/ChatService 后续发生两处变化，故其49项为明确版本的历史证据，不能把绿报告直接绑定上述全部当前输入。主线已记录后续相关 286 passed、Ruff/mypy 通过，以及全量 2881 passed/1 failed/3 skipped；唯一失败为路径外 test_rag_028_model_capability.py 的预算文案逐字相等断言与新增确定性未调用前缀冲突。该断言尚未获修改授权、尚未通过，必要门禁仍失败。后续当前版本回归、完整全量、最终独立审核、业务接受及最终候选 CI 均必须按实记录，任务不得据本报告关闭。本 Agent 未修改源码或测试。
