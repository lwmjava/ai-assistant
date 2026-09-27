# AGT-001～AGT-003：Agent 链路基础修复

> 状态：AGT-001、AGT-002、AGT-003 已实现。排在 `SEC-001` 之前的这三项已完成。
> 已确认：短路路径也执行工具，取代 SAND-002 中「不改变简单问题跳过工具循环的行为」这一非目标。
> 修订：2026-09-27 按评审意见修订历史截断、风险描述、格式错误的工具调用、拦截说明、共享循环、Trace、评测用例和验证命令。
> 二次评审：2026-09-27 收紧短路工具权限，补入历史脱敏、共享循环契约、Trace 数据最小化、独立 Evaluation 和跨入口测试矩阵。
> 终审：2026-09-27 按原子交付重排 AGT-002/003，补入显式执行策略、请求级去重、全 Trace 脱敏和无副作用 Evaluation。
> 任务：`tasks.yaml` 中的 `AGT-001`、`AGT-002`、`AGT-003`。
> 适用规则：`AGENTS.md`、`.cursor/rules/00-global.mdc`、`02-agent-runtime.mdc`、`03-tools.mdc`、`07-testing-evaluation.mdc`、`11-brownfield.mdc`。

## 目标

同一条会话里，助手给出选项后，用户只回「1」「好的」这类短句，助手能接着上一轮往下做；需要工具时工具会真正执行；工具循环不会把原始调用指令当成回答交给用户。

## 现状

默认编排是 `app/agents/pipeline.py` 的五阶段管线。会话历史已保存，并由 `ChatService` 放进 `AgentState.history`。历史超过 30 条后压缩：`history` 只留最近 5 条，旧消息的摘要放进 `state.context`。

| # | 问题 | 位置 | 后果 |
|---|---|---|---|
| 1 | 意图分流只把最新一句交给模型 | `_needs_plan`，`ChatMessage(role=USER, content=state.user_input)` | 「1」被判成简单问候，跳过规划和工具 |
| 2 | 分流结果用子串判断：回复里任意位置出现 `NO` 就短路 | `return "NO" not in (decision or "").upper()` | 模型回 `NOT SURE`、`I don't know`（`KNOW` 含 `NO`）也会短路。与文档字符串「解析失败时走完整流程」不一致 |
| 3 | 行动和最终回复的提示不带历史 | `_build_act`、`_build_respond` | 短路后模型只看见「1」，只能反问用户要做什么 |
| 4 | 短路路径只调一次行动，不进工具循环 | `run` 与 `run_stream` 中 `_run_action_once` 的短路分支 | 模型在短路里写出工具调用时，工具不执行，`<tool_call>` 原文被当成草稿交给最终回复 |
| 5 | 工具轮次用完时，最后一次输出仍是工具调用，直接成为草稿 | `_run_action_loop`、`run_stream` 行动循环、质量门循环 | 反思和回复拿到的是调用指令，回答可能带出 `<tool_call>` 原文 |
| 6 | 相同工具、相同参数可以在一次请求里反复执行，直到轮次上限 | 同上 | 模型卡在同一调用时白跑 5 次；有副作用的工具会重复执行 |
| 7 | `<tool_call>` 内 JSON 解析失败时，`parse_tool_call` 返回 `None`，循环把原文当最终草稿 | `app/agents/tools/base.py` `parse_tool_call`；各工具循环 | 格式坏掉的调用指令原样进入回答 |
| 8 | 工具循环有四份近似副本；流式质量门循环只发 `code_result`，不发 `tool` | `_run_action_loop`、`run_stream` 两处内联循环、`_quality_gate_loop` | 每项修复要改多处，行为容易不一致 |
| 9 | 工具执行不写 Trace | `AgentTrace.tool_call` 在 `app/` 中没有调用方 | 一次性接口的 Trace 看不到工具执行、跳过和轮次用完 |
| 10 | 只有本轮消息使用 `safe_message`；数据库历史原样进入 Memory 和理解 Prompt | `ChatService._build_memory`、`_history_messages` | 把历史新增到分流、行动和响应后，会扩大 PII、Token 等敏感内容进入 Prompt 的范围 |
| 11 | 短路与完整流程共用包含 MCP 的注册表，但 Tool 没有风险等级、权限和审批字段 | `ChatService._build_tools`、`Tool`、`mcp_tool_to_tool` | 简单请求可能执行未分级或有副作用的 MCP 工具 |

问题 1～4 从 Preflight 短路加入时就存在，与沙箱任务无关。

## 拆分

三条任务按顺序做。用户反馈的「回复 1 接不上」要三条都完成才会完整修复：AGT-001 让分流看见上一轮，AGT-002 先把工具循环护栏补齐，AGT-003 再安全地启用短路工具。

### 目标数据流

```text
数据库历史
  → 历史内容脱敏
  → Memory 窗口/压缩
  → 最近对话预算
  → 理解
  → Preflight（严格解析 YES/NO）
      ├─ full  → 计划 → 检索 → 受策略约束的共享工具循环 → 质量门 → 反思 → 响应
      └─ short → 安全工具白名单 → 受策略约束的共享工具循环 → 响应

共享工具循环：
  _iter_action_loop(state, execution_policy) → 逐步产出 tool/code_result，并写 state.draft
      ├─ _run_action_loop(state) 消费事件后返回 state.draft（一次性/Supervisor）
      └─ run_stream() 直接转发事件（流式）
```

### 共用：最近对话的截取规则

AGT-001 与 AGT-002 用同一个截取函数，结果是一段带角色标记的文本：

- 取 `state.history` 最后最多 6 条（历史压缩后最多 5 条）。
- 保护最近一轮：从末尾向前找最近一条助手消息及其之前最近一条用户消息。两条内容各最多 2000 字；更早的每条最多 300 字。
- 超长时保留开头和结尾，中间替换为「…（省略 N 字）…」。选项清单通常在助手回复末尾，代码通常在用户消息中段到末尾，首尾保留可以兼顾。
- 内容预算最多 4000 字，角色标签和省略标记另计。先从最早的非保护消息开始丢弃；仍超预算时，按剩余额度成对裁剪受保护的两条消息，不得整条丢弃上一轮用户消息或助手回复。
- 没有历史时写「（无历史对话）」。
- 长度是模块常量，不新增配置项。

### AGT-001 意图分流看见上一轮，并只认 YES 或 NO

- 问题：第 1、2、10 条。
- 方案：
  - `ChatService` 新增纯函数式历史脱敏 helper：不调用 `_apply_input_security`，不执行限流、注入检测、日志写入或安全上下文更新；无条件依次应用 `InputFilter` 与 `LogSanitizer`，任一异常返回固定占位。`_history_messages`、Memory 正常/回退路径和异步反思序列化都使用它。数据库原文不在本任务迁移。
  - 分流请求的用户消息改为「最近对话」一节加「用户最新消息」一节。
  - 分流说明补一条：最新一句是在回答上一轮的选项、确认或是否，且上一轮任务要推理、检索或调用工具时，回复 YES。
  - 只看回复开头的一个词（忽略大小写和前导空白）。`NO` 走短路；`YES` 和其它任何回复都走完整流程。
- 交付：
  - 历史进入 Memory、理解和后续 Prompt 前完成脱敏。
  - 共用截取函数与分流带历史。
  - 开头词解析。
  - Agent Evaluation 资产：Schema、数据集、index、独立运行器和机器可读报告。多轮用例是 Silver（AI 编写，不当 Gold）。
  - 确定性单元测试直接验证截取和分流结果解析；真实模型 Evaluation 才读取 `expected_route` 评分，被测模型的 Prompt 不包含 expected 字段。
  - 实现完成后写 `docs/plans/implementation_agt_001.md`，再将任务置为完成。
- 非目标：不改路由和兜底链。不新增配置项。不改理解阶段的 Prompt 结构（只脱敏其历史内容）。不改规划、检索的输入结构。不改 Supervisor。
- 验收：
  - 历史中的手机号、Token 和按字段名表示的密钥不进入 Memory 压缩、理解、分流、行动、响应或异步反思 Prompt；正常与 Memory 回退路径都覆盖；测试数据只使用合成哨兵。
  - 历史里助手刚给出 1 或 2 两个选项时，分流请求里能看到这段选项。
  - 助手回复超过 2000 字、选项在末尾时，分流请求里仍能看到选项。
  - 受保护的上一轮用户与助手内容都接近 2000 字时，两条都保留首尾，且内容预算不超过 4000 字。
  - 回复 `NO`、`NO。`、`no` 走短路；回复 `NOT SURE`、`NONE`、`I don't know`、空串走完整流程。
  - 没有历史、分流回 `NO` 的「你好」仍走短路，已有短路测试不改断言。
  - 真实模型 Evaluation 固定数据集、Prompt hash、模型与配置版本、评测器版本和 `temperature=0`，输出逐例结果、route accuracy 和 follow-up recall。关键续答用例 recall 必须为 100%，总体 route accuracy 不低于 90%；提供商不支持 seed 时在报告中记录。没有可用真实模型时任务不得宣称 Evaluation 完成。
  - Evaluation 不构建 ChatService，不连接 MCP/RAG/网络/沙箱或业务数据库；报告使用新文件并拒绝覆盖。

### AGT-002 共享工具循环与执行护栏

- 问题：第 5、6、7、8、9 条。
- 方案：
  - 合并工具循环并固定两层契约：`_iter_action_loop(state, execution_policy)` 异步产出 `tool`、`code_result` 并写 `state.draft`；`_run_action_loop(state, execution_policy)` 消费事件后返回 `state.draft`，保持一次性接口和 Supervisor 的现有调用契约。`run_stream()` 直接转发事件。
  - `execution_policy` 显式携带 `allowed_tool_names`；`None` 表示沿用完整流程现有工具集合。Prompt 展示的工具清单和执行前硬校验使用同一个策略，不依赖 `state.needs_full_pipeline` 隐式判断。
  - `AgentState` 保存本次请求已执行的工具指纹。初次行动、QualityGate 重跑和同一 Supervisor 调研状态共用该集合，避免循环重建后再次执行相同调用。
  - `parse_tool_call` 校验工具名必须是非空字符串、参数必须是对象；增加调用意图检测，使调用结果可区分「没有调用」「有效调用」「格式错误」。缺少闭合标签、多个信封、坏 JSON、非法名称或参数都属于格式错误。
  - 同一次请求里，工具名和参数（按键排序后的 JSON）都相同的调用只执行一次。第一次检测到重复时不再执行，追加「相同参数已执行过，请直接使用已有结果」后立即进入收尾。
  - 格式错误按一次失败调用处理：观测写「[工具调用失败] 调用格式无法解析」，计入轮次。
  - 轮次用完或进入收尾时，再调一次行动，提示里不列工具，并写明工具次数已用完、请根据已有结果写草稿。若仍含 `<tool_call>`，草稿改为固定句「工具调用次数已用完，未能得到最终结果。」
  - `max_tool_rounds <= 0` 时不执行工具，但仍做一次不列工具的行动调用，避免空草稿。
  - Trace 的 LLM 预览和异常字段统一先经过 `InputFilter`、`LogSanitizer` 与主机路径清理，失败时写固定占位。工具事件只记录工具名、`status`、轮次和耗时，不记录参数、参数指纹、工具结果或错误原文。
  - Supervisor 的调研节点复用 `_run_action_loop`，随之生效；它每次调研都新建状态，去重只在单次调研内有效。
- 交付：共享循环与显式策略；请求级去重；格式错误与轮次收尾；全 Trace 脱敏；流式质量门循环发出 `tool` 事件；`docs/plans/implementation_agt_002.md`。
- 非目标：本任务不启用短路工具循环。不改 `AGENT_MAX_TOOL_ROUNDS` 默认值。不做跨请求熔断。不改 Supervisor 的事件接口。不给流式接口补 Trace。
- 验收：
  - 脚本模型每次都返回同一个工具调用时，工具只执行 1 次，模型调用不超过「首次 + 一次重复 + 一次收尾」，最终回答里没有 `<tool_call>`。
  - 脚本模型每次返回参数不同的调用时，执行次数等于轮次上限，最终回答里没有 `<tool_call>`。
  - 初次行动已执行的调用在 QualityGate 重跑时再次出现，工具总执行次数仍为 1。
  - 坏 JSON、数字或数组名称、非对象参数、缺失闭合标签、多个信封都不执行工具，最终回答里没有 `<tool_call>`。
  - `max_tool_rounds=0` 时工具不执行，但能得到非空草稿。
  - 正常只调一次工具的用例，模型调用次数与现在相同。
  - `_run_action_loop` 仍返回字符串，Supervisor 调研节点的现有调用不变；Supervisor 继承去重和收尾语义，不获得流式事件。
  - 一次性接口的 Trace 中能看到工具执行、跳过和轮次用完三类记录。
  - Trace 的 LLM、工具和异常事件序列化结果不包含测试用手机号、Token、密钥或主机路径哨兵；工具事件中不存在参数值和结果字段。

### AGT-003 短路路径带上历史，并执行安全工具

- 问题：第 3、4、11 条。
- 方案：
  - 行动与最终回复的提示增加「最近对话」一节，用共用截取函数。完整流程与短路都带。
  - 历史单独成节，不写进 `state.context`。拦截函数 `reject_untrusted_tool_call` 看整段 `state.context` 和最新用户消息；本任务不改变该安全规则。
  - 短路路径调用 AGT-002 已完成的共享循环，并显式传入 `calculator`、`code_sandbox`、`get_current_datetime` 白名单。`web_fetch`、所有 `mcp__*` 和其它未分级工具不会出现在短路 Prompt 中，执行层也拒绝。
  - 短路仍跳过规划、检索、质量门和反思。
- 交付：行动与响应带最近对话；短路安全工具执行；真实模型工具选择 Evaluation；`docs/plans/implementation_agt_003.md`。
- 非目标：不改理解阶段。不把历史合并进检索上下文。不改检索查询。不改 Supervisor。不改前端。不在本任务建立完整 Tool 风险模型。
- 验收：
  - 用脚本模型复现：上一轮助手给出 1 或 2，用户回「1」。分流回 `YES` 时走完整流程并执行 `code_sandbox`；分流回 `NO` 时短路也执行 `code_sandbox`。两种情况都有 `code_result`，并由 `ChatService` 落库。
  - 上一轮用户消息里有 1500 字代码时，行动提示里的代码完整。
  - 短路无工具调用时模型调用次数与现在相同。
  - 短路白名单工具可执行；`web_fetch`、MCP 和未知工具不会展示且执行时被拒绝。
  - 检索不可信内容点名工具、用户原文没提的拦截测试仍通过。
  - 真实模型 Evaluation 的工具使用内存 recording fake；真实沙箱、MCP、网络、RAG 和数据库执行/写入计数均为 0。

> 与已有约定的变化：SAND-002 的非目标写过「不改变简单问题跳过工具循环的行为」。本任务有意改变这一点。产品方案第 219 行写明短路路径可跳过计划或工具步骤，但不得跳过代码沙箱（代码动作），本任务与之一致。

## 影响范围

- 代码：`app/agents/pipeline.py`、`app/agents/prompts.py`、`app/agents/tools/base.py`、`app/services/chat_service.py`、`app/debug/trace.py`。
- 测试：新增 `tests/test_agent_chain.py` 和 Agent Evaluation 校验；已有测试全部继续通过，重点是 `tests/test_supervisor.py`、`tests/test_tools.py`、`tests/test_llm_route.py`、`tests/test_agent.py`、`tests/test_workflow.py`、`tests/test_chat.py`、`tests/test_chat_controls.py`、`tests/test_sandbox.py`、`tests/eval/test_rag006_pipeline.py`。
- 评测：`evals/README.md`、`evals/schemas/agent_chain_case.schema.json`、`evals/datasets/agent-chain-v0.1/`、`scripts/run_agent_chain_eval.py`、`evals/reports/agent-chain-v0.1-*.json`。被测模型不读取 expected 字段；官方模式只调用真实 LLM，工具使用内存 recording fake，并禁用 MCP、RAG、网络、沙箱和会话持久化。报告是唯一允许的副作用，使用新文件且拒绝覆盖已有报告。
- 不改接口、数据库、前端和配置项，README 无需同步。

## 风险

- 分流、行动与回复的每次模型调用最多多带约 4000 字历史；一次请求会在工具循环和质量门重跑中重复携带，因此总 Token、延迟和费用增量可能是该值的数倍。本批只约束历史分区，不建立全局 Prompt 总预算。
- 分流看见历史后，走完整流程的比例会上升，延迟和调用次数随之增加。
- 当前没有完整工具风险模型。为避免扩大暴露面，短路只开放三个明确内置工具；完整流程中 MCP 与 `web_fetch` 的既有风险留给 SEC 系列。
- 分流说明和收尾提示是提示词改动，真实模型是否照做只能在页面或真实模型评测里确认。脚本模型测试只证明链路接通。
- 官方 Evaluation 使用合成数据与内存工具，不证明真实沙箱或 MCP 的生产质量；这些能力分别由既有沙箱测试和后续 MCP/SEC 测试证明。
- 开发环境的 Mock 模型回复以 `[mock]` 开头，改为开头词解析后一律走完整流程。原先用户消息含 `no` 时会短路。
- 模型失败时的固定提示以「完成」状态写进历史。原先只有理解阶段看得到，修复后分流、行动、回复都会看到，可能被当成助手说过的话。

## 残留风险

- 长会话误拦：历史超过 30 条后，压缩摘要放进 `state.context`。摘要里提到某个工具名，而完整流程用「1」检索命中任何带 `[UNTRUSTED_SOURCE]` 的资料时，用户回「1」触发的该工具调用会被拒绝。本批记录但不把已知错误固化为验收；留给 SEC 系列或记忆任务处理。

## 本批不做

| 内容 | 原因 |
|---|---|
| 超过 20 条后第 21～30 条不进上下文；摘要每次请求重算 | 属于 `MEM-001`，需先用回归证明行为错误再改算法 |
| 模型失败时的固定提示以「完成」状态写进历史 | 要新增消息状态，牵动接口和前端，另开任务 |
| 理解阶段带最多 20 条完整历史，没有字数上限 | 与本批截取规则不一致，统一上下文预算另开任务 |
| 检索用「1」这样的短句当查询 | 检索基线已冻结，改查询要走检索评测 |
| 调整不可信资料的工具拦截规则 | 安全规则变更，属于 `SEC` 系列 |
| 工具风险分级、副作用声明、审批 | 属于 `SEC` 系列 |
| 流式接口没有 Trace | `ChatService` 只在一次性接口创建 Trace，补齐另开任务 |
| 全局 Prompt 总预算 | 本批只控制新增历史分区；统一 Context Builder 需要 Harness ADR |
| 各阶段分别配模型、跨请求熔断、专家团、人工审批、状态持久化与恢复 | 改变 Harness 边界，需先写 ADR |
| Supervisor 路径带历史 | 属于 `SUP-001` |
| 默认打开质量门 | 另议，需评测数据 |

## 验证

每条任务实施前先用当前已存在的测试运行 `pytest` 和 `mypy app/`，记录基线。AGT-001 先新增 Evaluation Schema、数据集、运行器与失败测试并运行基线报告，再修改业务代码；新文件创建后才执行下面的目标命令。完成后重跑并对比，完成前不宣称通过：

```powershell
pytest tests/test_agent_chain.py tests/test_supervisor.py tests/test_tools.py tests/test_llm_route.py tests/test_agent.py tests/eval/test_rag006_pipeline.py -v
pytest tests/test_chat.py tests/test_chat_controls.py tests/test_workflow.py tests/test_sandbox.py -v
pytest
pytest tests/eval/test_agent_chain_dataset.py -v
ruff check app/agents/pipeline.py app/agents/prompts.py app/agents/tools/base.py app/services/chat_service.py app/debug/trace.py tests/test_agent_chain.py tests/eval/test_agent_chain_dataset.py scripts/run_agent_chain_eval.py
mypy app/
python scripts/run_agent_chain_eval.py --mode smoke
python scripts/run_agent_chain_eval.py --mode official
```

Evaluation 校验必须覆盖 Schema、index、数据集 hash、敏感信息扫描、被测 Prompt 不含 expected 字段、真实工具零执行和报告拒绝覆盖。`mypy app/` 或全量门禁若存在修改前失败，必须记录基线并证明未新增错误；无法隔离时按项目规则停止。

跨入口测试矩阵：

| 场景 | 一次性 | 流式 | Supervisor |
|---|---:|---:|---:|
| 正常一次工具调用 | 必测 | 必测 | 现有契约回归 |
| 短路安全工具 / 禁止工具 | 必测 | 必测 | 不适用 |
| 重复调用与收尾 | 必测 | 必测 | 必测 |
| 不同参数直到上限 | 必测 | 必测 | 必测 |
| 坏格式 / 工具异常 / 未知工具 | 必测 | 必测 | 契约回归 |
| `max_tool_rounds=0` | 必测 | 必测 | 契约回归 |
| 质量门重跑事件 | 必测 | 必测 | 不适用 |
| 流式停止后 `code_results` 落库 | 不适用 | 必测 | 不适用 |

页面核对：同一会话先让助手给出选项，再只回「1」，确认助手接着做，并出现「调用工具：code_sandbox」和结果块。真实模型 Evaluation 或页面核对缺失时，任务只能记录为未完成验证，不以 Mock 全绿替代。
