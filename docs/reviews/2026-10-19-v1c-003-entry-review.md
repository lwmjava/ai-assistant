# 技能跨路径入口独立反例审查

独立入口审查 Agent：`/root/v1c003_validation`。日期 2026-10-19。仅编写 `tests/test_skill_path_contract_review.py` 和本报告，未实现或修改业务代码；本 Agent 不是最终人工审核 Agent，不代行用户业务接受。

## 契约与证据范围

读取 AGENTS.md、适用技能与测试规则、任务交付技能、原任务卡、批准的实施计划与 ADR-0014，以及 ChatService/SkillService/AgentPipeline/Supervisor/PlanRuntime/StateManager 和现有技能、恢复、工具入口测试。

正式用例为 E2：真实 ChatService.chat/chat_stream，真实授权技能匹配与运行时/ToolExecutor，临时 SQLite 中有效 Tenant/User/Skill/Conversation/Message/AgentRunState，合成 LLM 和本进程工具。低分终态使用真实 local RAG 摄取与 MockEmbedding 64 维，仅证明接线与状态，不证明真实检索质量。通过工具函数副作用计数、模型实际 system payload、安全日志以及新 Session 回读消息和 checkpoint 对账。无真实业务数据、网络或付费模型。

覆盖同租户他人/跨租户私有隔离，默认/fast/真实 Supervisor 及双入口；Supervisor 两个 worker 私有围栏和调度节点无正文；匹配返回后接线前独立 Session 停用/改版；工具一完成时停用技能后二零调用；结构化计划越出绑定；缺失/畸形 checkpoint 零模型且保留旧历史；工具失败/取消元数据；低分终态不丢技能缺失/unsupported 说明；结构化 reason 实际消费围栏和版本。

## 实跑与修复前结论

解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。Windows PowerShell；主线程授予独占 pytest 窗口，未与其他 pytest 并行。

1. `-m ruff check tests/test_skill_path_contract_review.py` 首次退出 1，原因 `.ruff_cache` 临时文件 PermissionError；改用 `--no-cache` 后暴露导入排序与两行过长，修正测试格式后退出 0。缓存权限不是业务断言失败。
2. `-m pytest tests/test_skill_path_contract_review.py --basetemp data/pytest-tmp/v1c003-review-20261019 -q --tb=short`：退出 1，26 passed / 12 failed / 1 warning，15.47 秒，日志 `data/v1c003-baseline-20261019/review.txt`。其中两条失败是测试错误使用 `fail` 字符串，而执行器既有合法失败终态为 `error`，已改断言明确 `status=error`，不改变业务要求；其余 10 条为真实缺口。
3. 加入终态与 reason 后：`-m pytest tests/test_skill_path_contract_review.py --basetemp data/pytest-tmp/v1c003-review-red2 -q --tb=short`：退出 1，28 passed / 18 failed / 1 warning，17.08 秒，日志 `data/v1c003-baseline-20261019/review-red2.txt`。

确认以下 P2（已向主线程报告，并由主线程落修复计划，不由本 Agent 修改业务）：

| 问题 | 正式反例结果 | 修复验收要求 |
|---|---|---|
| 畸形绑定恢复删除旧记录 | list mode、bool/NaN version、字符串工具集合，双入口共 8 条：新 Session 原 checkpoint 已不存在 | typed 拒绝穿透且零新模型；原 status/JSON/stage/round 及用户消息保留 |
| 取消缺少技能执行终态 | 默认结构化工具取消双入口：日志只有 matched/bound | 明确 cancelled，不能当 executed；保持取消传播 |
| 早拒答丢技能状态说明 | self/fast RAG/Supervisor × 双入口共 6 条：只有低分拒答，无绑定缺失/unsupported 说明 | 同时保留拒答及确定性技能说明，来源空，不启动工具 |
| 结构化推理步未接技能 | 双入口 reason system 不含已选私有技能正文/围栏 | 实际 reason 模型 payload 含相同授权快照围栏；准确版本有安全日志 |

当前是修复前结论，**尚未独立复验通过**。原有 ast.NameConstant 弃用 warning 单独保留。最终全量回归、输入指纹、最终审核 Agent、用户业务接受和 Ubuntu CI 由主线程另行对账；本报告不声明这些已完成。

## 修复后独立复验（覆盖上文修复前状态）

按主线程 `2026-10-19-v1c-003-review-fixes.md` 的修复实际复读：Service 在旧存储读取器前执行同主体/租户/消息的活动 checkpoint 技能绑定预检，冲突不会进入 discard；结构化执行记录取消并传播；三路径早拒答合并技能状态说明；结构化 reason 向实际模型传递 runtime 授权围栏。

独占运行：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/test_skill_path_contract_review.py --basetemp data/pytest-tmp/v1c003-review-green-20261019 -q --tb=short`：**退出 0，46 passed / 1 warning，16.24 秒**。原始日志 `data/v1c003-baseline-20261019/review-green.txt`。未删除反例或降低断言。所有 18 项真实缺口转绿；新 Session 实际证明 checkpoint JSON/status/stage/round 与原用户消息保留、零模型，工具一成功后工具二零调用，取消不冒充成功、低分终态仍保留准确提示，reason 与两个 Supervisor worker 获得围栏，控制节点没有正文。Ruff 单文件 `--no-cache` 退出 0。

本轮 E2 入口反例独立复验通过，无剩余已发现 P1/P2。该结论绑定以下复验候选输入；业务/测试输入后续变化必须由主线程重新验证，不能把本轮覆盖到新代码。这里的单文件复验不替代全量回归或最终审核。

| 文件 | 复验候选 SHA256 |
|---|---|
| app/services/chat_service.py | ca38fd365fcac0f3d1b0285d69c2ad0c9759df9c4a6ca2aa501bf9f689bcd6ab |
| app/services/chat_skills.py | 80ee1cfa1e782f66e13bbfe5f9427e19adc6074649b79758fe7e8ae016ce22ef |
| app/agents/skills/base.py | 509c6639299f5b8aa651ccf0ffedb32e620553b016470dd19fe721f8d1abb381 |
| app/agents/skills/manager.py | 8a62d8c363b568c1b42a997c6d52cf259f0901e6d3c5a9c98bf0f5b8b57641b4 |
| app/agents/pipeline.py | 67125b66890d9bbb19f4d951341d3377334ca7796e5860dd1b9afbc24d293ec5 |
| app/agents/plan_runtime.py | f7044aa6535b7e322d0979f2451f45e95d6f1b3becadd28430b7278d9bd926cf |
| app/agents/state.py | 8b5a03a2809d78afda661675e239dcaa684e0ccb5f8bee8a6efb0fab64cc5112 |
| app/agents/supervisor.py | 3d7845b7f1e020e7e20ec9e6d609a98865d1adadada4f2c4d6c2162225f79f1f |
| app/agents/fast_path.py | ce781b0266eac768555a7627ea1c3463624defe3ada3b26630935091509320cd |
| tests/test_skill_path_contract_review.py | 5dc35a3aab0fb83fd193706ce8b349e27cf47671c702e82f856bb1250f725a0e |

## 追加恢复与流式执行边界（再次发现缺口，前轮通过不覆盖本轮）

主线程要求对已批准恢复契约补充分支反例，本 Agent 追加 7 项真实入口用例，未改业务。真实 SkillManager.activate/SkillRuntime.binding 构造当前有效版本绑定；合法结构化计划第一项已成功，cursor=1、round=1、budget 未耗尽、一个成功指纹，原 checkpoint suspended。正式断言要求续跑只执行第二项、预算最终为2、保留成功输出/指纹，无新增用户消息或重新语义选择。

首轮新增6项：`-m pytest tests/test_skill_path_contract_review.py -k bound_resume --basetemp data/pytest-tmp/v1c003-resume-review-red -q --tb=short`：退出1，4 failed / 2 passed / 46 deselected，4.53秒，`data/v1c003-baseline-20261019/resume-review-red.txt`。

再增加消费者 `tool.call` yield 后独立 Session 停用私有技能的反例：`-m pytest tests/test_skill_path_contract_review.py -k 'bound_resume or tool_call_yield' --basetemp data/pytest-tmp/v1c003-resume-review-red2 -q --tb=short`：退出1，5 failed / 2 passed / 46 deselected / 1 warning，5.23秒，`data/v1c003-baseline-20261019/resume-review-red2.txt`。

新发现：

- P2：有效版本恢复依然重新语义匹配，原工具技能没有词面关键词时被当作无技能并拒绝；双入口同版本应直接恢复已绑定快照，不能让选择模型重新裁决。
- P2：请求开始前配置改Supervisor被现有guard拒绝（2项通过）；但prepare后memory await期间配置改Supervisor，后续实际开新图，忽略旧checkpoint而继续生成（2项失败）。须在执行构造/恢复关口拒绝，不产生新模型/工具，保留原行。
- P1：流式 `tool.call` 事件发出后、消费者独立Session停用技能、迭代恢复时，第二工具函数仍实际执行。技能校验位于可让出控制的yield之前，未覆盖实际新工具启动。须在最后yield之后紧贴executor调用复核，保留已成功前缀、不重放副作用。

文件当前53项；上述5失败尚待修复复验，**当前不能引用前轮46通过声称最终入口审查通过**。

## 最后追加复验（覆盖前述待修复状态）

又补充合法身份版本但篡改 `mode` 或 `tool_names` 的双入口恢复反例4项；断言结构合法但语义不匹配的binding在模型/工具之前拒绝，保留原JSON、状态、阶段、轮次和用户消息。

本 Agent 独占实跑完整文件：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/test_skill_path_contract_review.py --basetemp data/pytest-tmp/v1c003-review-final2 -q --tb=short`：**退出0，57 passed / 1 warning，18.32秒**，日志 `data/v1c003-baseline-20261019/review-final2.txt`。单文件 Ruff `--no-cache` 退出0。

实际确认：恢复通过当前授权manifest重新构建原版本上下文，零语义重新选择；原成功前缀、cursor、预算、指纹保留，仅执行第二工具；配置在prepare后切换Supervisor安全拒绝，零模型与工具且旧行原样；流式tool.call让出控制后独立SQL停用技能，下一次迭代在executor紧前再校验，函数零调用且已成功前缀原样保留。篡改模式或绑定即使身份版本合法也拒绝。之前46条仍全部通过。未降低断言、未跳过失败。

已只读核对fast日志路径通过 `pipeline.skill_path = "fast.tools"` 区分实际路径，未改变绑定授权集合；该变化已包含在本轮57项复验输入。独立入口审查结论：**已发现的P1/P2全部修复并复验通过，无剩余入口阻断**。最终完整回归、最终审核、用户业务接受和最终提交Ubuntu CI仍不由本报告冒领。

本轮候选指纹如下，替代前轮46项指纹用于本轮入口证据；之后相关输入若再变化必须重跑：

| 文件 | 最后入口复验 SHA256 |
|---|---|
| app/services/chat_service.py | 87c7df813fb2d10b750f8b3c141cf9d229dc7f25ea67c9516c73e57b4ac87421 |
| app/services/chat_skills.py | e82bbad488e163df32c6901a2ae24c1241e6df36fa8caa413d7e6716205ea7eb |
| app/agents/skills/base.py | 3be6f64cd524d8af0a500897af37259a13208b34f95df0f285bc79ebb165f465 |
| app/agents/skills/manager.py | 8a62d8c363b568c1b42a997c6d52cf259f0901e6d3c5a9c98bf0f5b8b57641b4 |
| app/agents/pipeline.py | 6c01ffadd8097cfc77447c2b071b11a5a6de48c439cd97c725923e9aa20f83fd |
| app/agents/plan_runtime.py | 943d9ebcbd93a6a5d4b5c88cb2ea73783ed7c47641297f1c7acbbece9f99b6a1 |
| app/agents/state.py | 8b5a03a2809d78afda661675e239dcaa684e0ccb5f8bee8a6efb0fab64cc5112 |
| app/agents/supervisor.py | 3d7845b7f1e020e7e20ec9e6d609a98865d1adadada4f2c4d6c2162225f79f1f |
| app/agents/fast_path.py | 9b14904199c70ea04201f47970509a3234b2f672437bb1e53fe6dfc45149b359 |
| tests/test_skill_path_contract_review.py | ce3abe5406e2cd04472d523b603994e3ea04a02682929266b735c9208f2bce1d |

## 全量兼容修复后的只读复核

主线程报告首全量出现两个旧 `SupervisorGraph.__new__` 测试未经过构造器，直接访问 `skill_runtime` 导致失败；随后增加类级 `skill_runtime: SkillRuntime | None = None` 兼容默认值。本 Agent 实际只读检查当前 Supervisor 完整 diff 与类定义：默认值为不可变 None，不建立共享请求状态；正常构造器仍将请求内 runtime 写到实例，worker、早拒答和 typed 拒绝逻辑仍在。没有发现此兼容补丁引入的 P1/P2。

原57项独立实跑属于上表历史输入，**不是当前Supervisor版本的独立实跑**。当前Supervisor SHA256 为 `7a71bfa91268d261cca3ef50127c6c478ed0d6b1ad4ae2f82dbccd3a9756805a`。内存移除类级两行后 hash 与原57项旧指纹不同，因此本 Agent 不凭指纹声称已经重建、逐字节证明唯一差异；当前完整输入应以主线程最终证据清单绑定。

实际读取主线程 `data/v1c003-baseline-20261019/final-compat.txt`，汇总为 **123 passed / 1 warning，29.02秒**；主线程说明覆盖本文件57项、主线36项以及旧errors/P0用例。该123项是主线程实跑，不记为本 Agent 实跑。当前只读复核未运行 pytest，避免与主线程回归共享数据库。后续全量和新的最终审核 Agent另记，不由本独立入口角色替代。

### 兼容补丁指纹差异已独立解释

主线程指出兼容补丁同时新增了 `SkillRuntime` import（供类级注解使用）。本 Agent 已独立在内存中同时逆转 import 新增名字和类级默认两行，所得 SHA256 精确为 `3d7845b7f1e020e7e20ec9e6d609a98865d1adadada4f2c4d6c2162225f79f1f`，与原57项输入一致；当前源文件仍为 `7a71bfa91268d261cca3ef50127c6c478ed0d6b1ad4ae2f82dbccd3a9756805a`，未写回文件。由此确认本文件较原57项版本仅这两处兼容变化，上文未能解释的指纹差异已消除，不存在据此怀疑其他隐藏变化的剩余事项。

另实际读取主线程 `contract-final2.txt`：370 passed / 1 warning，61.75秒。123项及370项均归属主线程实跑；本 Agent 的57项仍标历史，未运行新的pytest。无剩余已知入口P1/P2；最终全量与最终审核状态以主线程最终证据为准。
