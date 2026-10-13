# PAGT-006 代码评审

日期：2026-10-13。评审对象：当前 HEAD `a0d02eb411a53c3191e81f0fc99528ea60d0d3c4` 的 PAGT-006 实现及真实调用入口。前置基线 `0b08c2e`。本轮没有参与该实现；未修复业务代码，未改 tasks.yaml，未提交或发布。

**结论：不建议通过或关闭任务。发现 3 个 P1、4 个 P2。现有测试通过，但尚不能证明原卡要求的中断续跑、预算和失败降级正确。** 2026-10-12 人工审核报告的“无未修复 P1/P2”不能沿用为本轮结论；该报告写的“实现未提交”也已成为历史状态，当前实现已在上述 HEAD。

依据：AGENTS.md、迭代指导、Harness 治理规范、任务契约、适用 Cursor Rules（全局、Agent Runtime、Tools、Skills、Testing、安全）、ADR-0010、实现计划/说明、既有测试和实际代码。应用 `code-review-quality` 技能，按执行层、状态层、入口和测试分块审查；没有为了满足观察数量而虚构问题。

## 发现

### F01 / P1：checkpoint 比工具指纹更新早，恢复会重复执行已完成工具

位置：`app/agents/pipeline.py:495-500`、`:869-871`。

`_finish_tool` 先把观测放入 state 并提交 checkpoint，返回后动作循环才写 `executed_tool_fingerprints`。若下一次模型请求期间进程/连接中断，已提交快照含结果却没有最后一次调用的指纹，`round_no` 也少一轮。这不是工具调用期间不可消除的崩溃窗口，而是每次正常提交的快照本身就不一致。

本轮用真实 Pipeline 动作循环、AgentStateService 和独立内存 SQLite：完成一次合成工具调用后，在下一轮抛 CancelledError，再用新 Session 读取并续跑。实测 checkpoint 有 1 条结果、指纹为空、round_no=0；相同工具总执行两次。对带副作用工具会造成重复操作。

建议：在一处统一完成观测、代码结果、指纹和轮次更新，再写 checkpoint；正式回归应从真实动作循环生成快照，不能手工预填正确指纹再证明恢复。

### F02 / P1：真实对话入口无法重新绑定原消息，恢复能力不可达

位置：`app/services/chat_service.py:319`、`:390`、`:302-306`；`app/services/quota.py:151-156`；`app/api/routes/chat.py:35-39`。

每次 chat/chat_stream 请求都先新建 Message，再用新 ID 注入 state_binding。请求契约只有 message/conversation_id，没有恢复原消息的标识；也没有其他恢复调度调用方。客户端断开或服务重启后重发请求，永远读取新消息对应的 checkpoint，原 running 行无法通过现有入口恢复。

本轮实跑真实 accept_user_message：同会话、同文本第二次请求获得不同 ID；旧 ID 能读到活动行，新 ID 返回 None。现有正向测试直接绑旧 ID 并调用 `_resume_state`，绕过了这一入口问题。

建议：提供受控的同一次执行恢复入口或调度机制，校验主体/租户/会话/原消息归属，复用原消息 ID 且不再次计入消息配额。API 或恢复调度方案需先同步契约/ADR；跨消息隔离修复应保留。

### F03 / P1：续跑忽略已用轮次，重新获得完整调用预算

位置：`app/agents/pipeline.py:463-465`、`:482`、`:848`；`app/services/agent_state_service.py:123`。

服务读回 `_round_no`，StateManager 不恢复该字段，动作循环始终从 1 跑到 max_tool_rounds。恢复的 tool_budget_exhausted 也只影响回复说明，不会阻止新的调用。用指纹数量代替轮次还不能表示非法/被拒绝等已消耗轮次。

本轮反例：总上限 3，持久化已用 2 轮及两个不同指纹，恢复后仍执行 3 次新参数调用，总量变成 5。反复恢复可以持续刷新预算。

建议：持久化并恢复真实轮次/剩余预算，执行前扣减同一执行的总预算；已耗尽时只允许无工具收尾。覆盖“部分预算恢复”“耗尽后恢复”“连续两次中断”，并区分既有 QualityGate 预算语义与本轮新增恢复问题。

### F04 / P2：短路径正常结束时跳过终态 checkpoint

位置：`app/agents/pipeline.py:671`、`:693`、`:921-922`、`:938-939`。

非流式意图短路径和检索终态短路提前 return，流式同类分支在 done 后 return，都跳过函数尾部终态写入。尤其短路径已经使用工具时，用户收到成功回复，库里仍留下 running 行，随后会被 load_active 当成未完成执行。

本轮实跑非流式、流式意图短路径：两者完成后数据库 status 都是 running；流式事件已包含 done。现有测试只校验事件类型，未检查数据库终态。

建议：让所有成功/失败出口统一提交对应终态，明确处理取消，避免在 finally 中把取消执行误标 done；覆盖短路径、检索拒答和完整路径。

### F05 / P2：流式恢复读库异常在 try 外，无法按承诺降级

位置：`app/agents/pipeline.py:897-898`；`app/services/agent_state_service.py:101-102`。

run_stream 在 try 之前调用 `_resume_state`，而 load_active 的查询没有异常处理。数据库读失败会直接抛出，无法进入正常流式错误/结束处理，更无法按实现说明“checkpoint 写/读失败不阻断主链”继续。非流式虽然捕获异常，却会直接结束管线，不是全新运行降级。

本轮在 store 的 load_active 注入合成 RuntimeError，收集 run_stream 时异常直接逸出；未发出任何事件。此为故障注入证据，不冒充真实数据库锁争用实测。

建议：在恢复边界捕获读取失败、回滚失败事务后按全新状态运行；同样校验快照字段类型，避免合法 JSON 但 tool_results=null 等残缺数据污染 state。

### F06 / P2：strict_validation 未拦截 object/array 错误类型和 bool-as-integer

位置：`app/agents/tools/executor.py:74-81`。

校验器只处理四种标量，没有 object/array 分支；Python bool 属于 int 子类，因此 True 被当成 integer/number。即使契约 strict_validation=True，错误数据仍进入 func。

本轮严格工具参数 `obj=1, arr="bad", num=True`，分别声明 object/array/integer，实测 status=ok、func 执行 1 次。

建议：补齐已经承诺的基础类型检查，对 integer/number 排除 bool；无需扩大为完整 JSON Schema，但实际支持范围必须与说明一致。

### F07 / P2：TTL 只有读取时丢弃，旧消息状态不会实际清理

位置：`app/services/agent_state_service.py:110-112`；`app/core/config.py:375`。

ADR-0010 承诺“惰性读 + 启动/定时删除过期行”。仓库搜索 AgentRunState/agent_run_states/AGENT_STATE_TTL 仅找到模型、接线和本服务，没有过期清理调度。服务只在读取同一旧 message_id 时检查 TTL，而 F02 表明正常新请求不会访问该行，done/error 行也同样逐消息累积。state_json 含上下文和工具结果，expires_at 不是实际删除机制。

证据为代码和调用方搜索，未执行生产数据清理或长周期留存实验。

建议：在服务层实现有界过期清理并接到明确的启动/调度生命周期，覆盖无人再次访问的过期行、新鲜行保留、失败与租户边界；不能把 TTL 配置当成保留期已实现。

## 验证与可复现产物

统一解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。现有 pytest 使用 `data/test_ai_assistant.db`，独立反例使用自行创建的内存 SQLite；没有访问业务数据库、网络、真实模型或凭据。

| 实际命令（python 为上述解释器） | 退出码 | 结果 |
|---|---:|---|
| `python -m pytest tests/test_tool_executor.py tests/test_agent_state.py tests/test_agent_state_resume.py -q --basetemp=data/pytest-tmp-pagt006-review-20261013` | 0 | 35 passed，3.13s，1 条既有 ast 弃用警告 |
| `python -m pytest -k "agent or tool or chat or quota or rag_log" -q --basetemp=data/pytest-tmp-pagt006-review-wide-20261013` | 0 | 279 passed / 1505 deselected，29.72s，同一警告 |
| `python docs/reviews/pagt006_review_probe_20261013.py` | 0 | F01–F06 的缺陷现象断言全部命中；输出 JSON |

反例脚本：[pagt006_review_probe_20261013.py](pagt006_review_probe_20261013.py)。机器输出：[pagt006_review_probe_20261013.json](pagt006_review_probe_20261013.json)。脚本退出码 0 表示缺陷被复现，不表示实现合格。合成工具/脚本模型仅验证确定性状态与调用路径，不证明真实模型质量或生产恢复。F02 的入口结论由真实消息写入反例和 HTTP/ChatService 契约走查共同支持，未声称进行了真实 HTTP 断线重连实验。

## 范围、漂移和后续

本轮仅新增报告、反例脚本和机器输出三个文件；tasks.yaml 保持 in_review。测试通过不抵消上述反例，不自动做业务验收。应先修复三项 P1 与相关 P2，再复跑入口级恢复/取消/预算反例和回归，更新实现说明及关闭对账。

已知根 README/.env.example 配置缺失仍登记为原批准范围例外，本轮不越界修复。PostgreSQL、多实例并发、真实付费模型、生产数据/迁移/删除均未测试。ruff、mypy、全量 pytest 本轮未跑，不沿用旧结果充作本轮验证。未新增架构决定、未降低门禁、未自动修复或发布。撤销本轮产物只需移除上述三个评审文件，不涉及业务或数据回滚。
