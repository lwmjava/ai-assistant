# V1C-004 用户委托业务接受

日期：2026-10-19。独立业务接受 Agent：`/root/v1c004_business_acceptance`。本人为新建独立身份，未参与本卡实现、工程审查、入口审查或最终技术审核。

## 结论与授权

**用户明确委托的 V1C-004 业务接受通过。** 授权原文：“授权独立 Agent 代行 V1C-004 业务接受，验证真实工具执行、未调用及失败提示、权限与技能限制、跨轮预算、取消和审计；通过后记录结论。”

本人独立读取原始 tasks.yaml 交付/验收/非目标、AGENTS.md、task-card-delivery v1.3.0、必读治理入口和适用规则、批准实施计划与 ADR-0015、README 支持矩阵、关闭对账及最终机器证据，直接核查 Supervisor/委派器/Service/行动循环/执行器代码与全部本次测试的断言；依据本次实际业务入口结果作出判断，未以既有最终审核或测试数量直接替代业务结论。

此结论覆盖当前冻结候选的隔离业务行为，不代表整卡已经关闭或允许发布。任务保持 in_review，最终候选 CI 尚待提交后核验；本轮没有 Git 提交、推送、合并、生产或外网动作。

## 实际业务接受对账

| 用户委托场景 | 实际验证及用户可观察结果 | 独立判断 |
|---|---|---|
| 真实工具执行 | 普通/流式 ChatService 进入真实 LangGraph research，经既有行动循环和 ToolExecutor 执行本地函数；调用列表等于真实参数，模型实际接收 TOOL_RESULT_4，持久化回复完成；流式有 tool.call 和 ok 子任务摘要；新 Session 读到主体/租户一致的审计。另跑实际 calculator 技能绑定执行与成功元数据。 | 通过，模型文本不充当执行证据。 |
| 未调用及失败提示 | 合成模型故意声称完成但无调用：函数和审计均为零，最终保存回复确定性含 not_called；无注册表和无委派的旧构造同样不执行并说明。注入真实本地异常/睡眠超时，实际执行一次，新 Session 审计存在，回复及流式子任务明确 error/timeout，内部异常标记不外泄。部分成功后失败保留两类事实；工具成功后模型失败仍披露 ok。 | 通过，失败不能被模型伪成功覆盖。 |
| 权限及技能限制 | 无契约、L2、非法数值/重试契约、未开启审计、未知工具均零未授权副作用；拒绝审计存在，未知工具无成功审计。停用用户/租户、token/角色/租户变化后无工具启动。工具输出诱导另一工具不会获得授权，控制系统不消费恶意指令。真实 SQLite 私有技能在同租户其他成员及跨租户不可消费；允许技能仅进入 worker 围栏，控制系统不注入；绑定交集阻止未绑定工具。技能变版/撤销在提议后、stream tool.call 让出控制后及 retry 前均阻止新调用。 | 通过，技能和模型文字没有扩大原权限。 |
| 跨轮预算及重复 | 两轮 research 共享总尝试上限，预算1只执行首工具，最终回复含 budget_exhausted；预算0不调用工具和行动模型。相同参数跨两轮只执行一次，回复保留 ok/skipped。非法数值预算拒绝为 E3 补充，不替代上述真实入口 E2。 | 通过，不按 research 轮次刷新额度、不重复成功副作用。 |
| 取消 | 普通/流式执行中取消会传给实际异步函数，finally 结束，后续工具及 draft 均不启动；新 Session 审计含 cancelled。tool.call 处关闭外层流时工具未启动，嵌套 producer 已回收，无 draft。执行器退避中取消保持单条取消审计（该用例为执行器补充 E3，完整入口在途/关闭测试为 E2）。 | 通过，不承诺回滚已经发生的动作。 |
| 审计及新请求 | 实际成功、错误/超时、策略拒绝、取消、撤权 retry 均核查独立 Session 记录，与真实执行次数/主体核对；模型 provider 缓存真实命中后，新用户请求仍重新执行工具并产生第二条审计，未把旧成功回复当作新执行。 | 通过，审计授权放行字段不等同业务执行成功，最终执行事实由 ToolOutput 状态披露。 |

## 本人实际运行

PowerShell，解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0，Windows-11-10.0.22621-SP0，LangGraph 1.0.10（最终机器证据版本）。`DATABASE_URL=sqlite:///./data/v1c004-regression-test.db`、`PYTHONUTF8=1`；各正式入口用例再创建 tmp_path 隔离 SQLite。tests/conftest.py 清空真实模型凭据、屏蔽工作区 .env、禁止非 loopback 网络；业务故障均为合成输入与本地函数，不调用付费模型/生产资源。没有其他 Agent 并发 pytest。

```powershell
$env:DATABASE_URL='sqlite:///./data/v1c004-regression-test.db'
$env:PYTHONUTF8='1'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_supervisor_tool_contract.py tests/test_supervisor_tool_contract_review.py tests/test_skill_path_contract.py tests/test_skill_path_contract_review.py --basetemp data/pytest-tmp/v1c004-business-acceptance1 -q --tb=short
```

实际退出码 **0**：**178 passed /1 warning，59.80秒，无跳过**。警告为既有 sandbox.py ast.NameConstant 弃用，不影响断言。原始输出：[business-acceptance.txt](2026-10-19-v1c-004-business-acceptance.txt)，SHA256 `c28f711d2c20c466706925ce4f06b32070259dee65a69a1befeba5ba869b3de4`。本次复用正式测试，但由本人独立选择范围、逐项读断言、执行并解释结果；未改动原测试或业务代码。

## 候选绑定与适用边界

基线/HEAD：`e005b4d08b5139080423f780db8c60f92d5d219e`，分支 `codex/v1c-001-lazy-retrieval`，候选为未提交 HEAD 加 [evidence.json](2026-10-19-v1c-004-evidence.json) 的 input_sha256。

本人在测试前后分别逐文件重算全部 **526 项** SHA256，均零缺失/零不匹配；清单排序后 JSON 的 SHA256 为 `c87d721b74848bc3bfe5633d0b52a96f510a487b8157e46de16d9652f172a39a`。本报告和日志属于新增说明，不改变冻结源码、测试、配置、依赖或评测输入。

核心交付以 **E2 隔离集成证据**接受：真实 Service/Graph/SQLite/工具执行器/本地函数，合成模型用于确定性提出动作和伪成功反例。它证明本卡工具委派业务契约，不证明生产模型选工具准确率、真实外部服务质量、收费模型效果、RAG检索质量或生产性能。未操作前端：本卡没有前端或接口契约修改。既有全量2882通过/3跳过、Ruff/mypy及最终审核184通过是机器证据中已有技术门禁，本人没有声称重新运行这些命令。

无本卡剩余 P1/P2 业务接受缺口。原有重新生成等时间戳排序缺陷未在本卡修复；stream cache lease 安全旁路保持现状，不以此次缓存测试声称优化。跨实例状态、跨消息 exactly-once、Supervisor checkpoint恢复、任意并行、高风险自动执行及知识库/工具组合规划均未获本卡实现授权；不借此宣称 V1C-005 完成。最终候选 CI 是剩余关闭条件，本报告不豁免该门禁。

附带范围检查：本人执行 git diff --check，退出1，提示 README 与前卡 V1C-003 evidence 文档新增行的 CRLF 尾空白；本次新增报告/日志未在提示列表。未擅改其他 Agent 的文档，已报告主线处理；该文档格式问题不改变上述冻结代码的业务接受结果。
