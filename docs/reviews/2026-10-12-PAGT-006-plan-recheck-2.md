# PAGT-006 实施计划复审结论（第 2 轮：修订后核对）

- 复审对象：`docs/plans/plan_pagt_006_20261012.md`（实现者按第 1 轮审查结论修订后版本，路径不变）
- 第 1 轮审查：`docs/reviews/2026-10-12-PAGT-006-plan-review.md`（结论「有条件通过」，2 P1 + 4 P2）
- 复审日期：2026-10-12
- 复审人：独立计划审查 Agent（与第 1 轮同一审查身份，未参与实现、未参与本次计划修订）
- 方法：对照第 1 轮 6 项缺陷，逐条核对修订是否真实落位、与全文是否自洽（无残留旧表述、新用例名入验证表、S10 无越界、审计/校验落点一致）。
- 替代说明：同第 1 轮，以等价审查替代不存在的 `plan-eng-review` 技能。

---

## 1. 六项修订逐条核对

| 第 1 轮缺陷 | 修订落位核对 | 结论 |
|---|---|---|
| **P1-1** 缺正向续跑测试 | §2.6 新增 `test_resume_continues_from_checkpoint_not_restarting`：ScriptLLM 跑到 action 中段（echo 已执行、指纹集已含 echo）后模拟中断、checkpoint 已 upsert；新 run（新 Session/新句柄、同 conv+msg）续跑。断言：指纹集保留 echo、重复调用被指纹判 skip（func 计数器不增）、未完成轮次从断点继续、understanding/plan 不重跑、最终答案由续跑产生。§1 A2 行与 §6「新增状态层」行均已列入该用例。 | ✅ 落位，且正是 acceptance② 缺失的正向业务行为（非仅读回 JSON） |
| **P1-2** S10 越界改根 README/.env.example | §3 S10 整行划除，改为「**不修改**根目录 README.md/.env.example」，配置说明落 config.py 字段注释 + 本计划/实现说明；「未同步根目录文件」登记为已记录偏差，按 task-card-delivery 原样写入 tasks.yaml 卡备注与实现说明未同步项。§9 P1-2 同步记录。 | ✅ 落位，边界已收回 allowed_paths 内 |
| **P2-1** 观测拼装两份实现漂移 | S2：观测拼装（含打码/路径净化）收敛为唯一共享 helper（`base.py::format_tool_observation(...)`），Executor 与 legacy `Registry.run` 同源；S3：legacy 路径也改为调同一 helper。§2.5 新增 `test_bound_executor_observation_golden_matches_legacy` 断言 bound 与 legacy 逐字节一致。 | ✅ 落位，双路径 golden 同守 |
| **P2-2** 重试审计条数未定义 | §1 D1-超时 / A1 行均写明「一次逻辑调用（含全部重试）聚合为一条 TOOL_CALL，details 恒 risk/allowed/reason/latency_ms 四键」；§2.5 新增 `test_logical_call_with_retries_writes_single_aggregated_audit_four_keys`。 | ✅ 落位 |
| **P2-3** 校验结论落点未定义 | §1 D1-参数校验行与 S2 写明：校验结论**只写 `ExecutorResult.audit_metadata`（供 Trace 消费），不进审计 details、不落 arguments/密钥**；新增 `test_validation_conclusion_not_in_audit_details` 守门（配合 `test_executor_wrong_type_arg_recorded_in_metadata`）。 | ✅ 落位，落点明确、泄漏方向有守门 |
| **P2-4** non_goal① 表述漂移未对账 | §1 Non-goal① 行新增「对账澄清」：卡原文「新代码走 Repository、旧访问渐进迁移」在本卡落实为服务层薄封装，Repository 独立层不交付，与 ADR-0010 决定2 一致、非静默改写；§5 同步。 | ✅ 落位 |

第 1 轮 P3-1 笔误（§1 残留 `test_executo`）已订正。§9 原两条「待用户确认（非阻断）」已消解并写入修订记录，§9 末行声明「当前无阻断性待用户决策项」。

---

## 2. 复审新发现（1 处残留旧表述）

**R2-1（P3，文档一致性，非设计缺陷）：§1「配置项」行仍残留「.env.example/README 同步」旧表述，与 S10 修订矛盾。**
- 位置：§1 对账表「配置项（ADR-0010 决定3）」行（约第 55 行）——可观察期望列仍写「…`AGENT_STATE_CHECKPOINT_TOOL_ROUND=True`；**`.env.example`/README 同步**」，证据列仍写「文档同步 diff」。
- 问题：S10/§9 P1-2 已决定**不修改**根目录 `.env.example`/README 并登记为已记录偏差；该行却仍把「`.env.example`/README 同步」列为期望、把「文档同步 diff」列为证据，三处决策（S10、§9、§5）与此单元格自相矛盾。若不订正，实现者可能按此单元格再次去改根目录文件，重开 P1-2 越界。
- 修复要求（一句话）：把该单元格改为「3 个 `AGENT_STATE_*` 字段落 config.py（带字段注释）；**根 `.env.example`/README 不同步，登记为已记录偏差（见 S10/§9）**」，证据列删去「文档同步 diff」、改为 `test_agent_state_config_defaults` + 偏差留痕。此为一处单元格编辑，不涉及任何设计或边界决策变更。

**R2-2（P3，命名一致性，可随实现顺手订正）**：§1 D1-超时行 E2 列写 `test_retry_zero_matches_legacy`，与 §2.1 用例名 `test_max_retries_zero_matches_legacy_attempts_one` 不完全一致。建议对账表用例名与 §2 失败清单对齐，避免关闭对账时用例名悬空。

---

## 3. 总体结论

**通过。**

第 1 轮的 2 个 P1 与 4 个 P2 已全部真实落位，且在 §1 对账表 / §2 失败清单 / §3 实施步骤 / §6 验证方案 / §9 决策记录之间相互自洽：新用例名已进入 §6 验证表，S10 已收回 allowed_paths 内、不再越界改根目录文件，审计聚合与校验结论落点前后一致，non_goal① 已显式对账，无残留阻断性待决策项。

仅余 2 处 P3 文档一致性小项（R2-1、R2-2），均为单元格级编辑、不改变任何交付/验收/边界决策：
- R2-1 为避免重开越界，**实现者应在开工首个提交或实现说明中同步订正** §1 配置项行；
- R2-2 可随实现顺手对齐用例名。

二者不构成本轮计划阶段的阻断项，**无需再开第 3 轮计划复审**。

**PAGT-006 计划阶段就此闭环，可进入实现。** 实现阶段仍须遵守：正向续跑用例先行失败测试、共享拼装 helper、审计按逻辑调用聚合、三条不变量（4 内置工具零改动且 bound 路径 golden 逐字节相等 / AgentEvent 不新增类型 / 未注入走原路径）、E2 证据与新句柄读回、conda 解释器 + `data/test_ai_assistant.db` 隔离库、不提交/推送/合并/部署。
