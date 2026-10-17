# PAGT-004 最终独立人工审核：第 1 轮

日期：2026-10-13。审核身份：`/root/pagt004_manual_review_1`。本角色未参与本卡实现、计划审查、入口验证或修复复验；仅写本报告，不修改业务代码、测试或任务状态。实际读取并使用 task-card-delivery v1.3.0 的最终人工审核流程。

结论：**本轮工程验收通过，未发现新的未决 P1/P2。用户业务验收未明确委托，任务应保持 `in_review`，不得据本报告自动 `done`。** 本轮不是用户业务判断或生产发布批准。

## 独立依据与范围

从 `tasks.yaml` PAGT-004 原卡核对两个必交付项与两个验收项：结构化步骤含依赖及预期产出、可解析、逐步执行、准确单步失败定位。核对用户批准的 docs/ 与 app/llm/mock.py 范围扩展，以及 ADR-0011 固定顺序、固定参数、旧 checkpoint、显式文本回滚、不做动态参数引用。批准记录来自当前会话及 ADR/计划，不以实现说明代替原卡。

已读取 AGENTS.md、AI辅助开发迭代指导、agent-harness-engineering、适用规则 00/01/02/03/07/08/09/11、实现计划、ADR-0011、实现说明、关闭对账、Evaluation、独立入口报告、CE01–07 修复复验及最终证据 JSON。直接阅读 plan.py、plan_runtime.py、state.py、Pipeline 的生成/恢复/执行/双入口/超时路径，核对 Prompt、Executor、Mock 和 opt-in legacy fixture 的 diff，并读取真实 HTTP 入口测试，不只采纳实现者摘要。

当前工作区含 PAGT-006 既有 API/Service/main/配置/OpenAPI 等变更；本轮不把整个工作区 diff 归为 PAGT-004。未观察到本卡引入通用 DAG、另造 Runtime、直接访问数据库或新增权限。StateManager 仍通过既有注入协议访问持久化。

## 原始契约抽查

| 原始要求 | 实际入口与观察依据 | 判断 |
|---|---|---|
| 可解析步骤、依赖、预期产出 | 严格版本化解析；HTTP/ChatService 真实执行三个依赖步骤；新 Session 读取实际 checkpoint | E2 通过，Schema E3 仅作补充 |
| 逐步执行及具体失败定位 | 同步/流式第二步业务错误后仅调用前两步，step2 failed，后继 pending；实际审计和公开安全码 | E2 通过 |
| 旧路径和显式回滚 | HTTP 显式文本模式实际执行旧工具；旧 checkpoint 兼容；v1 取消恢复保留已完成工具和预算 | E2 通过 |
| 不改五阶段、不做 DAG | 顺序数组执行仍位于原 action 阶段；仅向前依赖；固定参数，无动态引用和并行拓扑调度 | 源码与批准 ADR 一致 |
| 失败边界与既有审查 | 非有限数值/规模、超时、权限拒绝、恢复参数绑定、敏感失败快照、游标/预算/指纹一致性、canonical 文本恢复均有正式回归 | 本轮专项通过；CE01–07 无未决项 |

独立入口报告中的早期四项领域失败不能单独当通过；已核对后续正式修复、独立复验和最终同指纹回归。PAGT-002 当前依赖复核具有 171 项开工证据及最终工具/审计回归，不仅凭 done 字段判断。

## 本角色实际执行

取得独占串行 pytest 时隙，使用指定解释器与测试数据库：

```powershell
$env:RAG_BACKEND='native'
$env:DATABASE_URL='sqlite:///./data/test_ai_assistant.db'
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest -k 'plan or tool or audit' -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt004-manual-review-1
```

结果：**退出 0，356 passed / 1622 deselected，21.03 秒，1 warning**。警告为 sandbox 既有 ast.NameConstant 弃用；deselected 是本轮明确专项范围，不是测试 skipped。本轮实际运行含 Plan 真实 HTTP/Service/Executor/SQLite、步骤失败、权限审计、恢复/回滚及正式修复反例。E2 使用合成 provider 与隔离库，不能声称真实模型生成质量。本角色未重复全量 pytest、ruff 或 mypy；那些结果分别来自已核对最终证据。

实际解析最终 JUnit `data/pagt004-full-final-20261013.xml`：1978 tests，0 failures，0 errors，0 skipped，428.061 秒。实际计算证据 JSON 中全部 **37 个文件 SHA256，0 项不匹配**。下列关键文件及 JUnit 指纹亦精确匹配：

| 文件 | SHA256 |
|---|---|
| app/agents/pipeline.py | b0fc989e742526d23f5089192581d45d1dd8116f50d5f8a230cefb8d84626e95 |
| app/agents/state.py | 57c78269832fbc02cdadf4968abb61c74d199eed09432582f9c4f4e678a5737f |
| app/agents/plan_runtime.py | 888ee8eaff4821ba2dfee8be6c8c30a6b0d4de91306a936db31608e96bde15fa |
| tests/test_plan_entry_acceptance.py | 9725f8b20ecae395263b393dea43d0c7d01d002cc4f1d9329f60fa295a0762a3 |
| data/pagt004-full-final-20261013.xml | 077ede1b90ba743e8a5c14aecef9783dd6098ca103953f00dc99016abb27bd46 |

HEAD 为 a0d02eb411a53c3191e81f0fc99528ea60d0d3c4，候选为未提交工作区；本报告以文件指纹绑定最终候选，不用 HEAD 冒充全部实现版本。上述哈希核对和 JUnit 解析命令均实际退出 0；pytest 时隙已释放。

## 限制、回滚与最终状态

未验证用户业务判断、真实 LLM 规划遵循率/生成质量、真实外网服务可用性、PostgreSQL、多实例、生产性能及 checkpoint 写失败/断电窗口 exactly-once。原始必交付的确定性控制链具有 E2；这些外部/生产限制已原样写入关闭对账与 tasks.yaml，没有以 Mock 或测试数量推导生产可用。无前端变更，页面与构建不适用本卡。

已核对回滚构造参数停止新 v1 生成但保留已有 v1 恢复；完整旧代码回退前须核查活动快照与既有副作用。checkpoint 既有 fail-open 和断电窗口不能保证 exactly-once；本轮不升级该保证。本角色没有提交、推送、合并、部署、迁移或重要数据操作。

关闭对账的工程项可通过。此前“最终独立人工审核待执行”缺口已由本报告补齐；**用户业务验收仍未验证，保持 `in_review` 交用户决策**。没有批准自动开展下一张卡。
