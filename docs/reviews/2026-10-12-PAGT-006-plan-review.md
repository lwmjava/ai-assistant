# PAGT-006 实施计划审查结论（独立计划审查）

- 审查对象：`docs/plans/plan_pagt_006_20261012.md`（约 230 行）
- 原始契约：`tasks.yaml` PAGT-006 卡（:8885–:8939）
- 架构依据：`docs/adr/0010-agent-runtime-state-boundary.md`（Accepted，2026-10-12）
- 流程依据：`docs/ai-prompts/skills/task-card-delivery/SKILL.md`（v1.3.0）
- 治理依据：`AGENTS.md`（§4 Brownfield / §5 分层 / §7 工具十要素 / §10 mypy·SQLModel / §12 强制停止）
- 审查日期：2026-10-12
- 审查人：独立计划审查 Agent（未参与本计划编写、未参与实现；与实现者不同实例）
- 仓库：`E:\culture\SmartCustomerServiceSystem\ai-assistant`（Windows / PowerShell）

> **审查替代说明**：本仓库技能目录仅含 `module-batch-delivery` 与 `task-card-delivery`，**不存在 `plan-eng-review` 技能**。本审查按 `task-card-delivery` Workflow 步骤2 的能力降级条款，以等价审查替代 `plan-eng-review`：按「范围漂移 / 架构 / 兼容 / 权限 / 失败路径 / 测试与证据分级 / 性能 / 计划完整性」八个维度逐项核对，并直接对照原始契约检查范围漂移。本结论未调用、也不宣称调用过 `plan-eng-review`。

---

## 1. 审查范围与方法

1. 通读实施计划全文，与 PAGT-006 卡的 2 条 deliverables、3 条 acceptance、3 条 non_goals、risk/side_effect/rollback、evaluation commands 逐条对账。
2. 通读 ADR-0010 决定 1/2/3，核对计划的 Executor / StateManager / `agent_run_states` 表设计是否忠实落地已批准架构。
3. **代码事实抽查（不以计划摘要代替）**：实读 `app/agents/tools/base.py`、`contract.py`、`builtin.py`、`app/agents/pipeline.py`（AgentState/AgentEvent/`__init__`/`_execute_tool`/`_finish_tool`/`_iter_action_loop`）、`app/core/config.py`、`app/core/database.py:init_db`、`tests/conftest.py`、`app/models/__init__.py`，并 Glob 确认目标新文件均不存在、`README.md`/`.env.example` 实际位于仓库根。
4. **基线实测（只读）**：用 conda 解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（注意 python.exe 在 env 根，非 `Scripts\` 下）实跑 `ruff check .` 与 `mypy app/`，取得当前退出码。
5. 前置准入核对：`docs/reviews/2026-10-12-PAGT-002-head-recheck.md` 结论「通过、PAGT-006 可开工」（153 passed / 0 失败），与计划 §0 前置准入声明一致。

证据分级：代码事实为 E2（逐行读真实源码）；基线退出码为 E2（真实 conda 解释器实跑）。

---

## 2. 逐维度结论

### 维度 1：范围漂移（首要）——有 2 处需修正

- D1（Tool Executor：发现/校验/超时/重试/权限审计挂钩/结果归一化）、D2（State Manager：计划/已用工具/预算/续跑、支持流式）均在计划 §1 对账表，未被弱化或删为非目标。✅
- A1/A2/A3 三条 acceptance 全部映射入表。✅
- non_goal ②（不重写调用链）：计划取构造函数可选注入、legacy fallback 逐字节保留、接线 ≤3 处，符合 Brownfield。✅
- non_goal ③（不改 4 内置工具对外行为）：`builtin.py` diff 为空、观测 golden 守门入计划。✅
- allowed_paths：S1–S9 均落在 `app/agents/`、`app/services/`、`app/core/`、`tests/`，`app/models/` 与条件性 `alembic/versions/` 为 2026-10-12 用户批准扩展。✅
- **越界项（P1）**：S10 改根目录 `README.md` 与 `.env.example`，二者不在任何 allowed_paths（含批准扩展）内，且计划误判为「非阻断默认执行」。
- **表述漂移（P2）**：non_goal① 括号「新代码走 Repository」与计划「服务层薄封装、不抽 Repository」未做显式对账。

### 维度 2：架构边界——符合 ADR-0010 与 AGENTS.md §5

- StateManager 零 `sqlmodel`/`app.core.database` import，持久化经 `AgentStateStore` Protocol 注入 `app/services/agent_state_service.py`（持 Session）——agents 层不直接碰 DB，符合 ADR 决定2 与 §5。✅
- Executor 复用 Registry 既有 `_denied_reason`/`_record_audit`（组合委托、不复制 RBAC/审计逻辑），符合 ADR 决定1。✅
- **唯一架构级风险（P2）**：Executor 与 legacy `Registry.run` 各自拼装观测，未强制抽取共享拼装函数，存在 golden 漂移面。

### 维度 3：兼容——基本到位，bound 路径覆盖需补

- 4 内置工具观测 golden 回归（`test_builtin_tool_observation_golden_unchanged`）、AgentEvent 不新增类型（`test_agent_event_contract_unchanged_no_new_type`）、未注入走原路径（`test_registry_run_unbound_legacy_path_unchanged`）、`max_retries=0` 无重试与现状一致（`test_max_retries_zero_matches_legacy_attempts_one`）——均在 §2。✅
- **缺口（P2）**：golden 用例未明确要求经「绑定 Executor 的管线路径」跑一遍，可能守不到 Executor 侧重复实现的拼装漂移。

### 维度 4：权限与租户——到位，审计条数语义需补

- RBAC 组合委托、伪造身份参数拒绝（`test_forged_identity_argument_rejected`）、跨租户 checkpoint 不恢复（`test_checkpoint_cross_tenant_not_restored`）、越权角色拒绝（`test_l2_write_denied_for_unauthorized_role`）、审计 details 保持四键（`test_executor_denied_path_writes_same_audit_as_legacy`）——均在 §2.3。✅
- **缺口（P2）**：引入重试后「一次逻辑调用写几条审计」未定义、无断言，存在审计量相对 legacy 漂移的风险。

### 维度 5：失败路径（五类）——齐全，但 A2 正向续跑缺位

SKILL 规定的五类边界每类 ≥1 反例，计划 §2 全部覆盖，且每条给出用例名/注入方式/断言业务行为：

- 数值边界（§2.1）：超时、缺参放行、错类型、strict 拒绝、retry=0——5 条。✅
- 失败路径（§2.2）：幂等重试到上限、非幂等不重试、未预期异常转观测、损坏快照降级、持久化失败不破坏主链——5 条。✅
- 权限与租户（§2.3）：伪造身份、L2 越权、跨租户、无行全新——4 条。✅
- 身份与绑定（§2.4）：快照无密钥、过期忽略、事件契约、无 DB import、绑定不符丢弃——5 条。✅
- 回滚与幂等（§2.5）：upsert 单行、并发冲突不污染他行、golden 不变、开关关闭零行、unbound legacy——5 条。✅
- **缺口（P1）**：全部持久化用例都是 load_active 的负向/读回路径，**缺「中断后续跑」正向断言**——这正是 acceptance②「长会话可续」的核心业务行为。

### 维度 6：测试与证据——符合 E2 门禁

- 一律用 conda 解释器、隔离测试库 `data/test_ai_assistant.db`（conftest 已钉 `DATABASE_URL`/`AUTH_ENABLED`/`AGENT_ORCHESTRATION=self`/`SECURITY_RATE_LIMIT=false`）。✅
- 证据目标 E2，持久化要求「新 Session / 新 Python 进程读回」，明确 E3/E4 不得用于必交付项。✅
- 对账表 Non-goal 行用 E4/diff 审查——Non-goal 非必交付项，可接受。✅
- **基线实测（E2）**：当前 HEAD `ruff check .` 退出码 **0**、`mypy app/` 退出码 **0**。计划 §6「退出码 0」标准与现状一致、可达成；实现者须保持不引入新错（见 P3-3）。

### 维度 7：性能——checkpoint 写放大受控

- checkpoint 仅在工具轮结束（`_finish_tool` 后）与终态写，单次请求 ≤ `max_tool_rounds`(10)+1 = 11 次，无逐 token / 逐 LLM 阶段写；`test_checkpoint_written_only_at_round_and_terminal` 在 D2 行。✅

### 维度 8：计划完整性与一致性——表头合规，覆盖全卡

- 对账表表头与 SKILL 规定一致（原始要求及出处/输入-前置/操作链/可观察期望/实际证据/代码与环境版本/结果及缺口），覆盖卡内全部项并映射 ADR 决定 1/2/3。✅
- 授权依据（2026-10-12 用户批准扩展）落盘于 §0/§4/§9；无残留「待裁决/分支 B 执行路径」失效表述（分支 B 仅留作历史对照）。✅
- 回滚方式（配置开关 + 分层提交回退 + 不做 schema 降级）在 §7。✅
- 笔误与小项见 P3。

---

## 3. 缺陷清单（可操作，供实现者修复后复审）

### P1（必须在开工前修复）

**P1-1　acceptance②「长会话可续」缺正向续跑断言测试**
- 位置：计划 §2（失败边界清单）与 §1 对账表 A2 行。
- 问题：现有持久化用例全部是 load_active 负向/读回路径（无行→fresh、跨租户→None、过期→None、损坏→discard、upsert 单行、并发冲突、新 Session 读回字段）。没有任何用例证明「checkpoint 记录 round N + 已执行指纹集 {A} + budget 标志后，新 run 经 load_active 真正恢复并从 round N 继续——不重跑 round 1、不重复调用已完成工具 A、还原 tool_budget_exhausted」。**读回 JSON 字段 ≠ 管线消费该状态续跑**；没有该用例，acceptance② 的业务行为不可验证。
- 修复要求：在 `tests/test_agent_state.py` 增 `test_resume_continues_from_checkpoint_not_restarting`（沿用 `test_pipeline_budget.py::ScriptLLM` 模式）：用 ScriptLLM 在第 2 轮工具后中断，落 checkpoint；另起一次 run（新 Session/新进程）`load_active` 恢复后断言——已执行工具指纹集还原、`round_no` 从 N 继续、`tool_budget_exhausted` 标志还原、已完成工具 func 计数器不重复增长、最终 answer 走完。证据 E2。

**P1-2　S10 触达 allowed_paths 之外文件（根目录 README.md、.env.example），且被误判为「非阻断默认执行」**
- 位置：计划 §3 S10 行；§9 第 2 项。
- 问题：卡 allowed_paths = `app/agents/`、`app/services/`、`app/core/`、`tests/`、`docs/`、`tasks.yaml`；2026-10-12 用户批准扩展**仅及** `app/models/` 与条件性 `alembic/versions/`。实测 `README.md` 与 `.env.example` 均在仓库**根目录**，不在 `docs/` 内，亦不在任何 allowed_paths。计划 §3 括号「docs/ 与 README 在 allowed_paths」与事实不符；§9 把改这两个文件标为「待用户确认（非阻断，可在实现中默认执行）」——按 AGENTS.md §12，修改 allowed_paths 之外文件属**强制停止/需显式授权**，不得默认执行。
- 修复要求：二选一，在实现前明确——
  - (a) 取得用户显式批准把 allowed_paths 扩展到根 `README.md` 与 `.env.example`；或
  - (b) 本卡**不改**根 README/.env.example，3 个 `AGENT_STATE_*` 配置项的文档同步改走 `docs/`（在 allowed_paths）内既有文档，或在实现说明「未同步项」中如实登记待授权后补。
  - 实现者不得在未获授权时默认修改根目录文件。

### P2（应在实现中修复，修复后复审）

**P2-1　观测拼装未强制共享同一函数，golden 守门可能守不到 bound 路径**
- 位置：§3 S2（Executor 自做观测拼装）、S3（legacy run 保留 inline body）；§2.5 golden/unbound 用例。
- 问题：bound（Executor）与 unbound（Registry.run）成为两条各自拼装观测的代码路径；若 golden 用例只走 unbound，Executor 侧重复实现的拼装漂移不会被守门捕获，违反 non_goal③「观测逐字节一致」。
- 修复要求：(1) 计划明确「观测拼装 + web_search/file_ops 打码 + code_sandbox 路径净化」抽取为**单一共享函数**，Executor 与 legacy run 均调用它，禁止 Executor 另写一份拼装逻辑；(2) golden 用例须显式覆盖**绑定 Executor 后经管线 `_execute_tool` 产出的观测字符串**与基线逐字节相等，与 unbound legacy 各一条。

**P2-2　重试场景的审计条数语义未定义、无断言**
- 位置：§1 D1/A1 行（声称「一次调用一条 TOOL_CALL」）；§2.2 `test_idempotent_transient_retries_up_to_contract_max`。
- 问题：legacy 一次逻辑调用写一条 TOOL_CALL。Executor 引入重试后，attempts==3 时写几条审计未规定——若每次 attempt 写一条，审计量相对 legacy 翻倍/三倍，与 ADR「details 只落四键、一次调用一条」语义漂移。
- 修复要求：明确「一次逻辑工具调用 = 一条 TOOL_CALL」（聚合最终 status / attempts / latency），并增断言 `test_retried_call_writes_single_audit_row_not_per_attempt`：attempts==3 时审计表恰一条 tool_call、allowed=True、details 仍四键。

**P2-3　ADR「校验结论记入审计 metadata」落点未定义**
- 位置：§9 第 1 项；§1 D1-参数校验行；§2.1 `test_executor_wrong_type_arg_recorded_not_blocked`。
- 问题：计划把校验结论放 `ExecutorResult.audit_metadata`「供 Trace」，但未说明它是否真正写入 Trace/审计，还是仅活在内存 result 上即丢弃。若仅内存持有，ADR 决定1「校验结论记入审计 metadata」未达成。
- 修复要求：明确 audit_metadata 的持久/可见落点（写入 AgentTrace 记录或审计扩展字段，且不落 arguments/密钥），并在对应断言中校验该结论在 Trace/审计中可查，而非仅 result 对象字段。

**P2-4　non_goal①「新代码走 Repository」与计划「服务层薄封装」表述漂移未对账**
- 位置：§1 Non-goal① 行；§5 Non-goal 确认第 1 条。
- 问题：卡原文 non_goal① 为「不做 Repository 独立层（新代码走 Repository、旧访问渐进迁移）」，计划改写为「新代码走服务层薄封装，不抽 Repository 基类」。这与用户已批准的 ADR-0010 决定2（持久落入 `app/services/agent_state_service.py`、不抽 Repository）一致，但与卡面括号字面不同，未做显式对账，存在静默改写契约之嫌。
- 修复要求：在 Non-goal① 与对账表显式注明——「按 ADR-0010 决定2（2026-10-12 用户批准），新代码走 services 层薄封装、不抽 Repository 基类；卡面括号『新代码走 Repository』为远期迁移方向，本卡按 ADR 执行」。

### P3（观察项，不阻断，记录即可）

- **P3-1**：§1 D1-超时/重试行 E2 列有残词「`test_executo`（见 §2 失败路径组）」，应删去。
- **P3-2**：§6 验证标准「ruff/mypy 退出码 0」——本审查实测当前 HEAD ruff=0、mypy=0（conda 解释器），基线干净、标准可达成；建议实现者在实现说明记录**起始基线退出码**，并保持不引入新错。
- **P3-3**：`AGENT_STATE_CHECKPOINT_TOOL_ROUND` 配置项已声明，但 `test_agent_state_config_defaults` 建议一并断言其默认 True 及置 False 后「工具轮不写 checkpoint、仅终态写」的行为。
- **P3-4**：回滚后旧代码不识别 `agent_run_states.status=running` 的残留行，§7「须人工核查进行中的 run」建议补一句可选清理动作（如把残留 running 行标 error），非必须。
- **P3-5**：calculator 缺参友好文案等 golden 字符串，实现时应从源码取真实字符串做快照，勿手抄（本审查实读 `builtin.py:136-233` 与计划 §0 描述一致）。

---

## 4. 总体结论

**有条件通过。**

计划整体架构忠实于 ADR-0010、分层符合 AGENTS.md §5、契约对账表覆盖 PAGT-006 全部 deliverables/acceptance/non_goals/risk/evaluation、失败边界五类齐全、证据目标 E2 与隔离测试库门禁到位；计划声称的代码事实经逐行抽查全部属实，ruff/mypy 基线实测为 0。

但存在 **2 个 P1 + 4 个 P2**，须按「Review 驱动修复回路」在实现前/实现中修复并复审：

- P1-1（acceptance② 缺正向续跑测试）与 P1-2（越界改根 README/.env.example 且误判为非阻断）为开工前必须闭环项；
- P2-1～P2-4 在实现阶段修复，修复后连同 P1 一并复审。

P1/P2 未修复并复审通过前，**不得进入实现或关闭本卡**。

---

## 5. 对实现阶段的具体约束建议

1. **先闭环 P1 再写业务代码**：补正向续跑测试（P1-1）应先于 Executor/StateManager 实现落地，作为失败测试先行；根 README/.env.example 按 P1-2 二选一明确授权后再动。
2. **共享观测拼装**：把观测拼装/打码/路径净化抽成单一函数，legacy run 与 Executor 共用，杜绝两条拼装路径。
3. **审计按逻辑调用聚合**：重试不产生多行审计；details 恒为 risk/allowed/reason/latency_ms 四键，校验结论走 Trace/审计扩展字段且不落 arguments/密钥。
4. **守住三条不变量**：4 内置工具函数体零改动且经 bound Executor 路径 golden 逐字节相等；AgentEvent 不新增类型；未注入 Executor/StateManager 时管线逐字节走原路径。
5. **证据纪律**：持久化一律新 Session/新 Python 进程读回；E3/E4 不得支撑必交付项；pytest/ruff/mypy 用 conda 解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，测试库固定 `data/test_ai_assistant.db`，不得误用 `data/ai_assistant.db`。
6. **Brownfield 边界**：接线 ≤3 处、不重写 `_iter_action_loop`/`_stage`/`run_stream` 主体；StateManager 保持零 `sqlmodel`/`app.core.database` import；新表经 `create_all` 幂等建空表，alembic revision 按 §4.3 机制核对后再定是否落盘。
7. **回滚**：`AGENT_STATE_PERSIST_ENABLED=false` 即停 checkpoint；新表保留为空表/历史行，不做 schema 降级、不删数据、不删索引；夜间无人值守 `nightly.eligible=false`，不提交/推送/合并/部署。
