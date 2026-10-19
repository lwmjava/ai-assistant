# 技能跨路径接线：独立工程调查

日期：2026-10-19。审查 Agent：`/root/v1c003_engineering`。本轮为只读调查，不是尚未落盘实施计划的批准意见，也不是实现验收。未运行 pytest，避免与主线程共享测试库冲突。未改业务、tasks.yaml 或共享实施计划。

## 原始契约及已批准边界

依据 tasks.yaml 的 V1C-003、`docs/plans/plan_v1_chat_chain_closure_20261016.md` 第 V1C-003 节、AGENTS.md、task-card-delivery 1.3.0、相关 Cursor 架构/Runtime/Tools/Skills/测试/安全规则，以及 ADR-0004、ADR-0010、ADR-0011。

原卡交付 Prompt 与工具模式接线、匹配/注入/执行状态及版本证据；支持矩阵必须明确；私有隔离、版本变化、撤权、围栏必须验证。绑定不是授权，技能市场、自动淘汰、Supervisor 执行器扩展和 RAG/工具组合不属本卡。原卡尚无具体 allowed_paths，风险 L2，拆分计划明确具体方案及必要 ADR 须先批准。

## 代码事实

- ChatService `_match_skills_async` 已最多选一条，保留确定性优先、语义选择后的主体复核、候选重新读取与 effective_version 比较；非 builtin Prompt 套现有不可信围栏。匹配后的后续模型/工具 await 不因此自动获得持续技能撤权保障。
- SkillManager.activate 只为 prompt_injection 模式组装正文；tool 模式收集 tool_names。`collect_tools` 可查注册工具，但 ChatService 未消费；仅有 skill_names 落消息不证明执行或注入成功。
- self 管线和 langgraph SIMPLE/RAG/TOOLS 短路径已有 Prompt 注入；Supervisor 构造未接收技能上下文，research/draft 也未注入。
- Fast TOOLS 受 route.tool_names 白名单限制；self preflight 短路受既有短路白名单限制。完整自研路径既有结构化 Plan；它进入 `iterate_plan` 的分支早于自由行动循环 policy 的设置，故只改自由行动 policy 会漏掉结构化计划。
- `_wire_runtime` 使用传入原注册表独立构造 Executor。只替换 Pipeline.tools、而继续注入原 Executor，不能证明执行侧遵守技能绑定。
- StateManager 快照有旧 plan/context、预算及成功前缀，没有技能身份版本。Pipeline 恢复会把 context 替换为当前记忆，故不能认定旧 context 一定复用；但旧 structured_plan/tool_results 仍可能与新技能混用。已有成功前缀不可因技能变化丢弃后自动重跑。

## 推荐的最小待批准方案

1. Prompt 模式支持 self 完整/短路与 Fast SIMPLE/RAG/TOOLS，并补 Supervisor research/draft；不注入 supervisor 路由控制节点，保留既有安全与检索披露优先级、围栏及上下文预算。
2. 工具模式只在既有可执行工具的路径支持：self 完整、self 短路、Fast TOOLS。有效工具集合是技能绑定与既有路径策略交集，并继续经过既有注册表、Executor 权限/风险/参数/预算/审计；空交集不得隐式恢复全量工具。无技能保持兼容。
3. Fast SIMPLE/RAG 与 Supervisor 的工具模式明确 unsupported，说明工具未执行，不提升路线到新工具能力。缺失工具、执行 denied/error/timeout 与 matched/injected/executed 需要分别记录，不能用名称/模型自述替代结果。
4. 执行侧统一约束自由循环及结构化 Plan（含恢复计划、质量重写）；不要复制执行器。权限拒绝来自原 Executor，技能不可把角色拒绝改为成功。
5. ADR 明确技能新鲜度与恢复政策：至少将技能 origin/effective_version 及稳定身份与 checkpoint 绑定；恢复发现撤销或变版时显式拒绝继续并提示重发，不能 discard 后自动重做已成功副作用。历史缺少绑定的 checkpoint 与无技能请求的兼容策略必须明确裁决。可使用 AgentState 可选 JSON 字段与 StateManager schema 校验，不新增表迁移；Service 已调用 StateManager.valid_snapshot，可继续复用。身份版本冲突必须区别损坏 JSON，不走 discard 后新执行。
6. 明确状态证据落点及限长：优先沿用现有 Trace/协议 metadata，最小记录 name/origin/effective_version、路径、matched/injected/binding/result 状态，不记录技能全文/工具敏感参数，也不为本卡新增前端与持久化表。

最小修改预计涉及 chat_service.py、skills/base.py、pipeline.py、state.py、supervisor.py。若撤权要求覆盖执行中下一次调用，plan_runtime.py 必须在直接 executor.execute 前复用同一 Service 回调并记录执行状态，因为它绕过 pipeline._execute_tool；不能只在自由循环加保护。agent_state_service.py 在快照 schema 验证不变时可不改，最终以失败反例与具体实现计划核定。

以上是建议，不是已批准决定。具体工具绑定限制语义、unsupported 行为、撤权时点、checkpoint 兼容/拒绝策略及 allowed_paths 须在实施计划/ADR 具体化后由主线程完成批准流程。

## 必须纳入计划与验证的反例

- NaN/Infinity/极端 confidence 不得激活；绑定为空、重复、全部不存在、部分缺失应有确定结果，不退回全工具。
- 流式/非流式，从真实 ChatService 入口覆盖 self full/preflight、Fast SIMPLE/RAG/TOOLS、真实 Supervisor 与可选依赖缺失 fallback。
- 注册但无权的工具、结构化 Plan 点名未绑定工具、模型忽略白名单，均不得执行；成功/失败/超时/取消及审计的最终状态须可核对。
- 同租户其他成员及跨租户私有技能不可见；匹配/分流期间停用、删除或变版，执行前重读应拒绝旧授权/版本；禁用技能及外部候选不得改变原工具权限。
- checkpoint 同版恢复保留成功前缀与累计预算；变版/停用/删除明确拒绝，不重做副作用；历史快照按批准兼容策略验证。
- 不可信技能正文包含工具点名、安全规则覆盖、伪造系统段时围栏保留，实际工具选择不能从正文提权。Supervisor 控制节点不接收技能指令。
- 版本证据取实际执行快照，匹配无执行不显示 executed；持久化行为应以新 Session 回读证明，不能只检查对象。

## 本轮结论

可以继续落盘具体实施计划与 Proposed ADR，并做计划审查；当前没有足够批准依据改业务。未验证前置卡当前输入/CI、基线与最终业务，不声称前置闭合、本卡实现完成或技术验收通过。主线程提供落盘计划后，本 Agent 将针对具体文本复审，而非追认本次建议为已批准方案。

## 第二轮：具体计划与 Proposed ADR 复审

本轮实际读取 `docs/plans/plan_v1c_003_20261019.md` 与 `docs/adr/0014-skill-runtime-binding.md`。它们的原始交付映射、工具绑定收窄语义、Supervisor worker 限定、unsupported 矩阵、状态分离、allowed_paths 与禁止表迁移/前端/API/CI改动一致；没有把后续 V1C-004/005 功能带入。全量与独立审查门禁、无本卡 Git/业务接受授权也已明确。所有旧无绑定 checkpoint 拒绝恢复是明确列出的兼容变化，必须由用户批准，不能称现有 ADR 已授权。

批准前应澄清两项 P2 计划缺口：

1. **技能撤权关口**：目前写匹配后及首次接线前 fresh 复核，但原契约撤权和实际多步执行尚有空窗。应在每次将技能正文加入新模型调用、以及每次即将启动新工具动作时，经 Service 回调重新比对已选技能稳定身份/origin/effective_version/enabled；不得换成当前同名另一技能。工具一完成后技能停用，工具二必须拒绝，保留已发生副作用。此为离散执行前置校验，不是新增主体持续轮询，不承诺中止已在执行的工具；主体/角色权限仍交既有入口及 Executor。至少给出工具一成功后另一 Session 停用→工具二零调用的反例。
2. **恢复拒绝不能被兜底吞掉**：`_resume_state` 宽异常兜底会当成无快照，`run` 另有 error checkpoint 写入。版本冲突应使用明确 typed 拒绝，保证从 Service 或 Pipeline 向调用方安全终结，零新模型/工具；旧 checkpoint 及成功前缀不能被 discard 或以新 done/error 覆盖。计划应写明此保留断言，并覆盖 chat/chat_stream。

以上为第二轮修订前意见，后续第三轮结论覆盖其待澄清状态。当前不批准实现、没有运行新测试，也没有把主线程提供的基线结果当成本 Agent 实跑。

## 第三轮：修订复核与工程准入结论

已实际重读修订后的计划第3/4/6节和 ADR 决定1/5/6。计划明确每次技能注入、每次新工具启动前经 Service 离散校验，覆盖结构化执行与工具一成功后停用→工具二零调用；不做逐token/后台轮询、不撤回已启动动作。计划同时明确恢复冲突的 typed 拒绝须穿透宽捕获，在新模型/工具调用前结束，保留旧 checkpoint，不 discard、不覆盖为 done/error。ADR 与之对应。

两项 P2 **均已在设计文本关闭**，没有剩余 P1/P2 工程计划阻断。结论：**工程方案复审通过，可提交用户批准方案、allowed_paths 与 Proposed ADR 后实施**；这不构成用户批准或业务实现验收，仍未修改业务与运行新测试。

本轮实际审查输入 SHA256：

- `docs/plans/plan_v1c_003_20261019.md`：`3bb8b017f763998191c340e2d1d05d66012a64a80ee4f91e754308ee8e4f3cae`
- `docs/adr/0014-skill-runtime-binding.md`：`a936502690161281ee46406a5ac92b30b497d2de4ca3a3c79b3a9570f8e6ca3d`

实现后必须通过正式入口反例证明这些设计保证；计划意见通过不能替代测试、最终审查、用户业务接受或最终版本 CI。

## 第四轮：实现中静态复核（尚未冻结最终输入）

收到用户批准后，实际读取当前完整业务 diff、`app/services/chat_skills.py` 与两份新契约测试。未运行 pytest；验证 Agent 正独占测试库。此轮结论仅针对读取时版本，主线程仍修恢复入口，不给最终通过。

已确认的实现方向：Service 独立 Session 回调承担 DB/主体/技能版本复核；Agent 不 import SQL。自由行动及结构化计划都在执行前验证技能允许集；原 Executor 保留。Supervisor 仅 worker 注入，控制节点未追加技能；typed SkillBindingRejected 在 Pipeline/Plan/Supervisor/Fast 的主要宽捕获处有显式穿透。state_json 扩展且无表迁移。

发现并已通知主线程的待修复/核对项：

- **P2 空绑定终态**：`SkillRuntime.notice` 对工具模式 `tool_names=[]` 判断空集为任何 available 的子集，记录 binding_available 且无确定说明，与计划空交集显式提示冲突。应加入真实入口空绑定反例，保持零工具副作用并提示无可用绑定。
- **恢复快照可信性**：当前 validate_resume_skill_binding 仅比 identity；主线程正修“不重新语义匹配恢复”。应从授权清单按固定 identity 重建 mode/tool_names，再与 checkpoint 精确比对，不能直接信任畸形/篡改快照里的绑定。此为正在变动输入的防回归提醒，尚未判定最终版本缺陷。
- **P2 新增日志标识未约束**：SkillRuntime.record 直接记录拒绝工具的 call.name，既有 parser 只要求非空字符串，模型可给未绑定名字携带换行、长文本或凭据样式内容。新增安全元数据应仅记录合法有界标识，对非法名称用固定占位；不要求扩大修改旧 parser。应补未绑定恶意名字→零调用且 caplog 不含私密标记的反例。

恢复旧行保留、零模型前置、同版成功前缀及切换 Supervisor 拒绝等新反例由主线程/验证 Agent 正处理；不能以本轮静态阅读替代其执行证据。主线程提供修复后稳定输入与结果后再复核以上项。

## 第五轮：实现修复复核

已重新读取当前 SkillRuntime、chat_skills、ChatService 完整差异及 plan_runtime 执行关口；同时读取正式空绑定/恶意工具名测试和独立恢复反例。未运行 pytest，主线程正运行专项。

- 空绑定现在显式走 binding_missing 与用户固定提示，空允许集没有恢复为全工具：第四轮第一项 P2 已修复。
- 新增 record 只接受当前注册表内、符合有界标识正则的工具名，其他统一 unknown；拒绝日志不再消费模型伪造名称：第四轮日志 P2 已修复。
- restore_skill_context 从新 Session 当前授权清单按原 identity 重建 activate 结果，精确比较包含 mode/tool_names 的 binding，且不调用 selector。私有/全局正文重新加围栏。合法 identity 搭配篡改 mode/tools 无法恢复：第四轮恢复可信性提醒已落实。
- ChatService 普通/流式均在恢复时复用原选择；构造后若实际路径成为非 AgentPipeline，明确拒绝，不忽略旧进度进入 Supervisor。Service precheck 在原存储层可能 discard 之前拒绝绑定冲突。
- 结构化工具实际 execute 前在 tool.call yield 返回后又做 fresh check，覆盖事件消费者在暂停点撤权；异常显式穿透而不写新 checkpoint，原副作用前缀保留。自由工具执行也有执行前回调。

当前独立静态工程结论：**上述 P2 全部关闭，未发现剩余 P1/P2 实现阻断**。主线程提供的 red/green 和验证 Agent 57 passed 为他方执行证据，本 Agent未代称实跑；最终全量、输入冻结及最终独立审核仍待主线程完成。无本卡验收/发布/CI通过推断。

本次关键输入 SHA256：

- app/agents/skills/base.py：`3be6f64cd524d8af0a500897af37259a13208b34f95df0f285bc79ebb165f465`
- app/services/chat_skills.py：`e82bbad488e163df32c6901a2ae24c1241e6df36fa8caa413d7e6716205ea7eb`
- app/services/chat_service.py：`87c7df813fb2d10b750f8b3c141cf9d229dc7f25ea67c9516c73e57b4ac87421`
- app/agents/plan_runtime.py：`943d9ebcbd93a6a5d4b5c88cb2ea73783ed7c47641297f1c7acbbece9f99b6a1`

## 第六轮：Supervisor 可选上下文兼容复核

已实际读取 supervisor.py 最新完整差异及旧调用方 `test_error_model_boundary.py` / `test_p0_regression.py` 中使用 `SupervisorGraph.__new__` 的位置。新增类级 `skill_runtime: SkillRuntime | None = None` 为不可变空缺省，正常构造仍以实例属性覆盖，因而不会在请求间共享 SkillRuntime，也不改变具备真实实例上下文时的授权/注入路径。绕过构造器的旧调用方继续按无技能路径运行，符合可选参数兼容契约。

该兼容修复工程复核通过，未发现新增 P1/P2。未运行 pytest；主线程报告首轮全量 2 failed / 2795 passed / 3 skipped 属于修复前结果，不能作为最终版本通过。修复后专项 123 passed、Ruff/mypy 通过为主线程执行证据；最终专项与全量重跑仍由主线程记录。

本轮 supervisor.py SHA256：`7a71bfa91268d261cca3ef50127c6c478ed0d6b1ad4ae2f82dbccd3a9756805a`。
