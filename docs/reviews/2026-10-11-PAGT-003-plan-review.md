# PAGT-003 实施计划独立审查结论

- 审查对象：`docs/plans/plan_pagt_003_20261011.md`（工具调用预算与循环上限）
- 审查角色：独立计划审查 Agent（未参与该计划编写）
- 审查日期：2026-10-12
- 分支 / Git 基点：`codex/pagt-tool-polish`，HEAD `5c49368`（PAGT-002 提交）
- 审查基准：`tasks.yaml` PAGT-003（4673–4718 行）原始契约；AGENTS.md §4/§5/§7/§9/§10
- 审查边界：只读核对代码 + 隔离基线测试；未改业务代码、测试、计划文件、tasks.yaml；未做 git 提交。唯一写入 = 本审查文件。

---

## 0. 结论速览

- **总体结论：需修订**（1 项 P2 待清；无 P0、无 P1）。
- 计划在范围、架构、for…else 核心设计、数值守卫、测试覆盖、身份绑定、验收映射七个维度整体成立，核心设计（for…else 区分「预算跑满」与「自愿/重复 break」）经代码走查**正确**，不存在误报/漏报。
- 唯一需修订项是一处**计划表述与控制流不符**（§2.6 声称预算文案「不影响评分输入输出」，实际 run() 先行动循环后 QualityGate，文案会进入被评分 draft）。这不是设计错误，但必须更正措辞并显式登记该边缘，不得让「无交互」的错误断言带入实现。

---

## 1. 代码事实抽查记录（实际命令 / 结果 / 退出码）

| # | 核对项 | 命令 / 方式 | 结果 | 退出码 |
|---|---|---|---|---|
| F1 | HEAD / 分支 / 工作区 | `git log --oneline -3`；`git branch --show-current`；`git status --short` | HEAD=`5c49368 feat(tools):…十要素契约…`；分支=`codex/pagt-tool-polish`；工作区仅未跟踪 `docs/plans/plan_pagt_003_20261011.md`（待审计划本身），无业务改动 | 0 |
| F2 | 卡片 eval 命令基线 | `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest -k "pipeline or budget" -p no:cacheprovider --basetemp=data/pytest-tmp/pagt003-review -q` | **111 passed, 1621 deselected, 1 warning in 15.00s**（实现前基线；尚无 budget 测试，符合预期） | 0 |
| F3 | 类默认轮数 | 读 `app/agents/pipeline.py:323` | `self.max_tool_rounds: int = 5`（硬编码默认） | — |
| F4 | 循环硬边界 | 读 `pipeline.py:775` | `for round_no in range(1, self.max_tool_rounds + 1):` | — |
| F5 | 循环退出路径 | 读 `pipeline.py:778-804` | ①`:778-779` intent=none→`return`；②`:781-784` 重复指纹→`break`(wrap_up)；③`:785-790` invalid/denied→`continue`(仍耗一轮)；④循环跑满→既有尾块 `:803-804` `if wrap_up and inspect(draft).status!="none": finalize` | — |
| F6 | 耗尽现状 | 读 `pipeline.py:732-746` | `_finalize_without_tools` 无论何因进入都记 trace `exhausted`(:743)；仅当草稿仍含 `<tool_call>` 才返回 `_EXHAUSTED_DRAFT`(:55/:744-745)；LLM 配合时返回普通草稿、无任何预算说明 → 现状 acceptance② 不成立（缺口属实） | — |
| F7 | 配置现状 | 读 `app/core/config.py:308` | `AGENT_MAX_TOOL_ROUNDS: int = 5`，无 ge/le、无类型守卫 | — |
| F8 | 既有校验范式 | 读 `config.py:291` 与 `:432-438` | `RAG_IMPORT_MAX_CONCURRENCY: int = Field(default=2, ge=1, le=1000)` + `@field_validator(..., mode="before")` 拒 bool/非 int\|str。计划拟新增的 AGENT_MAX_TOOL_ROUNDS 校验与之同构 | — |
| F9 | fast_path 旁路 | 读 `app/agents/fast_path.py:24,99,103,105` | `_TOOL_ROUND_CAP=2`；`:99` 硬编码 `pipeline.max_tool_rounds=2`（绕开 settings）；`:103` 复用同一 `_iter_action_loop`；`:105` `state.answer=state.draft` 逐字输出。计划内的文案改动对短路径同样生效 | — |
| F10 | chat_service 接线 | 读 `chat_service.py:295,315,378`；grep `_build_pipeline` | `:295` AgentState 每请求新建；`:315`(非流式)/`:378`(流式) `pipeline.max_tool_rounds=settings.AGENT_MAX_TOOL_ROUNDS`（仅 AgentPipeline 分支）；`_build_pipeline` 每请求构造 | — |
| F11 | PRD 对齐值 | 读 `docs/product/项目产品需求方案.md:541-546` | `agent.max_tool_calls: 10`（PRD 值 10 vs 配置默认 5，漂移属实） | — |
| F12 | .env.example | 读 `.env.example:223-225` | `AGENT_MAX_TOOL_ROUNDS=5` | — |
| F13 | 既有测试是否依赖类默认 5 | grep `max_tool_rounds\|AGENT_MAX_TOOL_ROUNDS` 全仓 `*.py` | 仅 4 处测试引用，**全部显式赋值**：`test_agent_chain.py:255 = rounds`、`:466 = 5`、`:591 = 1`、`test_rag_028_model_capability.py:843 = 1`。**无任何测试依赖隐式类默认 5** | — |
| F14 | 既有 exhausted 断言 | 读 `test_agent_chain.py:446-483` | 同参数重复调用→第2轮 skip break→尾块 finalize→trace 含 `"exhausted"`；`:466` 显式 `=5`。该路径非预算跑满，计划新增「预算耗尽后缀」不得在此路径出现（防误报） | — |
| F15 | 主路径顺序 | 读 `pipeline.py:639-642` 与 `:562-577` | `:639 state.draft = await self._run_action_loop(state)`（预算文案在此落 draft）→ `:642 state.draft = await self._quality_gate_loop(state)`（对含文案的 draft 评分）；`_build_respond:572` `## 回答草稿\n{state.draft}` 喂最终应答者 | — |

---

## 2. 契约对账（逐项）

| 原始契约（tasks.yaml 4673-4718） | 计划是否守住 | 证据 / 说明 |
|---|---|---|
| deliverable① 达上限后停止并返回明确原因 | 守住 | for…else 判定跑满 → finalize → 确定性追加 `_BUDGET_EXHAUSTED_NOTICE`（计划 §3.3） |
| deliverable② 上限可按配置调整 | 守住 | `AGENT_MAX_TOOL_ROUNDS` 已有 settings 接线（F10）；本卡补 Field(ge/le)+类型守卫（F7/F8） |
| non_goal 不做跨请求全局配额 | 守住 | 预算 = 循环局部 `round_no`，AgentState/管线每请求新建（F10），不查 user/tenant 配额表 |
| non_goal 不改 QualityGate 评分逻辑 | **基本守住，见 P2-1** | 未改 `_evaluate_quality`/阈值/重入结构（F15 证实仅注入点先后关系，非逻辑改动） |
| acceptance① 循环场景达上限后不再继续 | 守住 | 既有 `range` 硬停；计划补 E2 用例断言计数恰=预算、无第 N+1 次调用 |
| acceptance② 返回内容说明预算耗尽 | 守住 | notice 落 `state.draft` → `_build_respond:572` → 最终应答者（F15）；fast_path 逐字（F9） |
| risk L1 / rollback 回退预算约束提交 | 守住 | 纯代码 diff（pipeline/config/.env.example），无 DB 迁移 |
| allowed_paths = pipeline.py / config.py / .env.example / tests / tasks.yaml | 守住 | 计划 §7 明确不改 fast_path.py、tools/、audit/、chat_service.py、api/、README、frontend |
| forbidden = .env / 生产密钥 | 守住 | 未触碰 |

---

## 3. 分维度结论

### 维度 1 — 范围漂移：通过
- 计划声称不动 QualityGate 评分、不做全局配额、不改 tools/audit/chat_service/fast_path，经 F9/F10/grep 核实为真。
- 默认值 5→10：**属契约允许**。卡片 facts 即「PRD §5.1.3 定义 max_tool_calls=10，未在管线落地为硬约束」（F11），对齐 PRD 是本卡事实意图；且 F13 证明无既有测试依赖隐式默认 5，改动不破坏既有测试。生产侧 chat_service 从 settings 取值，默认 5→10 是放宽，计划 §6 已如实登记成本侧风险（L1 + le=100 上界兜底）。

### 维度 2 — 架构与兼容：通过
- 预算落在管线循环层、先于 `registry.run()`（F4/F5）：跑满后循环不再发起下一轮 LLM 行动 → 不再调用 run() → 无被预算「截断」的进行中调用、无审计缺口。
- PAGT-002（HEAD 5c49368）交互：已执行调用照常每条审计（本卡不改 base.py）；被拒轮次在管线层拒绝、本就不进 run()，与审计无关。前置回归基线 152 passed 已纳入（计划 §3.6/§5）。
- fast_path `_TOOL_ROUND_CAP=2` 旁路如实登记（F9），文件不在 allowed_paths，本卡不改；短路径耗尽仍走同一 `_iter_action_loop`，文案对其同样生效。
- QualityGate 重进循环重新获得预算：§2.4 已登记为边界、§7 列为 non-goal（默认关闭）。

### 维度 3 — 核心设计 for…else 区分：通过（设计正确）
逐退出路径走查（F5）：
- **自愿停止**：intent=none → `:779 return` 退出生成器 → **不触发 else** → 无后缀。✓
- **重复指纹 break**：`:784 break` → **不触发 else**（break 跳过 else）→ 无后缀；既有尾块 `:803-804` 照常 finalize、照常记 `exhausted` trace（F14 既有测试语义保持）。✓
- **预算跑满**：循环正常耗尽（经执行或 continue）→ **触发 else** → budget_reached=True → 追加后缀。✓
- `continue`（invalid/denied）不跳过 else：与 §3.2「被拒尝试也耗一轮」一致，跑满即视为预算耗尽，保守侧正确。

结论：for…else 能精确区分三种停止路径，**不会误报（自愿/重复 break 不给后缀）也不会漏报（跑满必给后缀）**。acceptance② 成立。附带观察：新增 budget 块的 finalize 与既有尾块 `:803-804` 在跑满路径上功能重叠（finalize 后 draft 必无 `<tool_call>`，第二处守卫 `!= "none"` 为假，二者天然幂等、不会 double-finalize）——见 P3-1。

### 维度 4 — 数值边界与配置：通过
- `Field(default=10, ge=1, le=100)` + bool/非 int\|str 守卫，覆盖：0/负值→ge 拒；101→le 拒；True→bool 守卫拒（防 pydantic 把 True 强转 1）；2.5→非 int\|str 拒；"abc"→过 isinstance 但 int 转换失败拒；"10"→正常转换；NaN→float 拒。完备。
- 与 `RAG_IMPORT_MAX_CONCURRENCY` 范式（F8）一致；.env.example 同步（F12）。命名保留 `AGENT_MAX_TOOL_ROUNDS`、不改环境变量名（避免破坏性配置变更），Brownfield 合理。

### 维度 5 — 失败路径与测试：通过
- 五类边界齐全，~14 条反例，每条断言业务行为（截断计数=预算、抛错仍耗预算、请求间隔离、实例隔离、自愿停无后缀、截断无副作用、审计逐条回归）。
- `pytest -k 'pipeline or budget'` 可执行性已实测（F2 基线 111 passed, exit 0）；PAGT-002 回归 `tool or audit` 纳入。证据分级 E2 标注合理。

### 维度 6 — 身份与绑定：通过
- 计数 = `_iter_action_loop` 局部 `round_no`，非任何实例可变计数器；AgentState 每请求新建（F10）、`_build_pipeline` 每请求构造 → 并发隔离为结构性（不依赖全局锁/单例）。配置绑定 Settings 实例。计划 §4-3/§4-4 用例直接验证请求间与实例间隔离。

### 维度 7 — 可执行性与验收映射：通过
- Red→Green→回归顺序、隔离 basetemp 命令、E1/E2 标注、git revert 回滚均明确；acceptance 两条映射到 E2。

---

## 4. 缺陷清单

### P0（阻断正确性）
无。

### P1（违反契约 / 行为错误）
无。

### P2（需修订后方可「通过」）

**P2-1 — §2.6「预算文案不影响评分输入输出」与控制流不符。**
- 证据：F15 `pipeline.py:639`（行动循环落 notice 到 state.draft）→ `:642`（`_quality_gate_loop` 对该 draft 评分）；`_build_respond:572` 再把含 notice 的 draft 喂应答者。
- 影响：当 `AGENT_QUALITY_GATE_ENABLED=true`（默认关）时，预算耗尽路径的 notice **会进入被评分的 draft**，并经 `_build_critique:719-725` 进入 critique 上下文；若评分低于阈值重跑行动循环，还可能在 fresh 预算下再触发一轮工具调用（受 AGENT_MAX_REVISIONS 上界约束）。
- 为什么是 P2 而非 P3：计划对系统行为下了**错误的无交互断言**，实现者若照此断言会遗漏该边缘；且「notice 注入点」本应是个显式决策点。这不是设计错误（默认关、有上界、不改评分逻辑本身），但措辞必须更正。
- 修订要求（二选一，实现前落盘）：
  - 方案 A（推荐最小改动）：保留 notice 注入 `_iter_action_loop`，但把 §2.6 改为如实表述——「QualityGate 开启时，预算耗尽文案会进入被评分 draft 与 critique；受 AGENT_MAX_REVISIONS 上界约束、该开关默认关闭，作为已登记边缘接受」，并在 §6 未验证项补此条。
  - 方案 B（更干净）：把 notice 注入移到 `_quality_gate_loop` **之后**（主路径 finalize/应答阶段），使评分输入不含系统提示；fast_path 仍逐字输出 draft。
- 验收：计划更正该断言并显式记录注入点决策后即可关闭。

### P3（观察项，不阻断）

- **P3-1**：新增 budget 块的 `if budget_reached and inspect(draft).status!="none": finalize()` 与既有尾块 `:803-804` 在跑满路径功能重叠。因 finalize 后 draft 必无 `<tool_call>`，二者幂等、不会 double-finalize；实现时可简化（仅保留 notice 追加，finalize 交既有尾块）。
- **P3-2**：§0.1 称 `git status --short` = 空，实际含未跟踪待审计划文件本身（F1）。属计划落盘时点先后，非缺陷。
- **P3-3**：README 配置同步不在 allowed_paths（计划 §7 已登记为缺口）。依 AGENTS.md §11「配置变更需同步 README」，建议本卡关闭后开一张小卡/附注跟进 README 的 `AGENT_MAX_TOOL_ROUNDS` 说明，勿在本卡越界改。

---

## 5. 总体结论

**需修订。** 计划整体质量高：契约对账准确、Brownfield 边界守得住、for…else 核心区分设计经代码走查正确、数值守卫完备、测试可执行性已实测、并发隔离为结构性。无 P0/P1。

唯一阻断项为 **P2-1**：更正 §2.6 关于「不影响 QualityGate 评分输入输出」的错误断言，并显式决定 notice 注入点（推荐方案 A 如实登记边缘，或方案 B 后移注入点）。P2-1 修订落盘后，本计划可判定为**通过**并进入实现；P3 各项作为观察项随实现记录即可。

### 对实现阶段的附加约束建议
1. 实现 notice 注入时，先确认 QualityGate 开启/关闭两条路径下 draft 内容，确保耗尽路径给后缀、自愿停与重复 break 路径**不给**后缀（对齐 F14 既有测试）。
2. 新增 `test_pipeline_budget.py` 须沿用脚本 LLM 模式，所有断言针对业务行为；跑完须连同 `pytest -k "tool or audit"`（基线 152 passed）回归不回退。
3. `AGENT_MAX_TOOL_ROUNDS` 的 `@field_validator` 写法对齐 `config.py:432-438`（bool 先行拒绝，再限 int\|str），勿把 ge/le 校验用 try/except 重写。
4. 实现说明落盘时，如实登记 P2-1 采用的方案 A/B、P3-3 的 README 跟进入口，以及「真实 LLM 是否把后缀自然转述」为未验证项。
