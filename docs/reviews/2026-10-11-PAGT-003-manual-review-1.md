# PAGT-003 独立人工审核结论（第 1 轮）

- 审核对象：PAGT-003「工具调用预算与循环上限」最终实现（分支 `codex/pagt-tool-polish`，未提交改动）
- 审核角色：独立人工审核 Agent（**未参与**该卡实现、计划审查与入口级反例审查）
- 审核日期：2026-10-12（文件名按任务约定为 `2026-10-11-PAGT-003-manual-review-1.md`）
- 审核基准：`tasks.yaml` PAGT-003（4673–4718 行）原始契约；AGENTS.md §7/§9/§10/§12/§13；PRD §5.1.3
- 关联文档：`docs/plans/plan_pagt_003_20261011.md`、`implementation_pagt_003.md`、`closure_pagt_003_20261011.md`、`docs/reviews/2026-10-11-PAGT-003-plan-review.md`、`2026-10-11-PAGT-003-counterexample-review.md`
- 边界：只读核对 + 隔离复跑 + 自造复验脚本；**未改业务代码、测试、docs、tasks.yaml；未做 git 提交**。唯一写入 = 本文件及审核临时脚本 `data/pytest-tmp/manual_review_pagt003_mine.py`。
- 前任人工审核 Agent 中途停滞被终止，其证据（复验脚本、basetemp）经我**独立重跑复核**后引用，未直接采信其结论。

---

## 0. 总体结论：**通过**

无 P0、无 P1、无 P2 阻断缺陷。实现与原始契约、计划（P2-1 方案 B）一致；我独立复跑的全部命令退出码与结果同关闭对账记录一致；我自造的 21 项业务复验用例全部通过。5 项未验证项如实登记，无伪证据、无「未验证也关闭」。

---

## 1. 原始契约逐项核对（验收基准 = tasks.yaml 4673–4718）

| 原始要求 | 代码事实（我亲自读 diff/源码核实） | 我的复跑证据 | 结论 |
|---|---|---|---|
| deliverable① 单次请求达上限后停止并返回明确原因 | `_iter_action_loop`（pipeline.py:796-829）`for round_no in range(1, self.max_tool_rounds+1)` 硬边界 + `for…else: state.tool_budget_exhausted=True`（:824-827，`return`/`break` 不触发、`continue` 照常触发，层级正确）；`_apply_budget_notice`（:755-767）幂等追加 notice | 我脚本 M3：循环跑满 rounds=2，执行恰 2 次、无第 3 次；draft 尾部确认为「…已达到工具调用次数上限 2 次，后续内容基于已有工具结果生成。）」 | 通过 |
| deliverable② 上限可按配置调整 | `AGENT_MAX_TOOL_ROUNDS: int = Field(default=10, ge=1, le=100)`（config.py:308 区）+ `@field_validator(mode="before")` 拒 bool/非 int\|str（config.py:442-447）；pipeline `__init__` 默认 5→10（:329） | 我脚本 M2：True/0/-1/101/2.5/"abc" 均抛 ValidationError；"10"→10、100 接受；既有测试 `test_rounds_attribute_drives_truncation_point[2/3]` 通过 | 通过 |
| acceptance① 构造循环场景，达上限后不再继续 | 同 deliverable① | 我脚本 M3/M6 + 17 用例全绿 | 通过 |
| acceptance② 返回内容说明因预算耗尽而停止 | notice 经 `_apply_budget_notice` 落 `state.draft`；run/run_stream 均在 `_build_respond`/`stream_chat` 之前注入 | 我脚本 M3：answer 回显证明 notice 进入「最终应答者」输入（`## 回答草稿` 转述链）；draft 中 notice 恰一份 | 通过 |
| non_goal 不做跨请求全局配额（属 QUOTA-001） | 预算 = `_iter_action_loop` 局部轮次；AgentState/管线每请求新建；无任何 user/tenant 配额表读写 | 我脚本 M6：两独立管线/计数各自跑满 rounds=2，c1=2、c2=2，互不共享 | 通过 |
| non_goal 不改 QualityGate 评分逻辑 | diff 全文核对：`_evaluate_quality`/`_build_critique`/阈值/重入结构**零改动**；仅在质量门**之后**新增注入点（P2-1 方案 B） | 我脚本 M4：质量门开启（score=0.1 触发重入），发给「质量评估员」的提示**不含** notice；最终 draft notice 恰 1 份 | 通过 |
| risk L1 / rollback=回退预算约束提交 | 纯代码 diff（pipeline.py +29/-1、config.py +12/-1、.env.example 文案）；无 DB 迁移、无 schema 变更 | git revert 即可回滚 | 通过 |
| allowed_paths = pipeline.py / config.py / .env.example / tests / tasks.yaml | `git status --short` 仅 .env.example、pipeline.py、config.py 改动 + tests/test_pipeline_budget.py 新增 + docs 未跟踪；fast_path.py、chat_service.py、tools/、audit/、api/、README、frontend **零改动**；tasks.yaml 未改（按流程由主 Agent 验收后更新，属预期） | git status/diff --stat | 通过 |
| forbidden = .env / 生产密钥 | 未触碰；`.env` 不在 git status | — | 通过 |
| facts：PRD §5.1.3 max_tool_calls=10 落地为硬约束 | 读 `docs/product/项目产品需求方案.md:543`：`max_tool_calls: 10`；代码默认已对齐 10 | M1：新构造管线实例 `max_tool_rounds==10` | 通过 |

## 2. 关键机制走查（源码级，非仅看 diff）

1. **for…else 三路径区分**（pipeline.py:796-829）：自愿停（:800 `return`）与重复指纹 skip-break（:805 `break`）**不触发** else → 不置标志、不带 notice；跑满（经执行或 invalid/denied `continue`）→ 触发 else。我脚本 M5 验证自愿停不带 notice；既有 `test_trace_records_tool_statuses_without_secrets`（同参重复→skip-break→尾块 finalize→trace 仍记 exhausted）回归通过（agent_chain 41 passed 内含此用例），无误报泄漏。
2. **注入点四处**（方案 B，均在 QualityGate 之后、最终应答之前）：run() 短路（:610）、run() 完整路径（:650，在 `_quality_gate_loop` 之后）、run_stream() 短路（:858）、run_stream() 完整路径（:911，在质量门重入块之外）。被评分 draft 不含 notice（M4 实证）。
3. **幂等**：`_apply_budget_notice` 先查 `tool_budget_exhausted` 标志、再查后缀已含即返回；反例审查 CE-5 连调两次仍恰一份。
4. **配置校验范式**与既有 `RAG_IMPORT_MAX_CONCURRENCY`（config.py:291/432 区）同构；bool 先行拒绝堵住 pydantic `True→1` 强转陷阱（M2 实证）。
5. **失败/被拒轮次照耗预算**：boom 抛错工具（实现者用例）、畸形格式、短路 denied 均跑满才截断，不因异常提前/延后停。

## 3. 关键文件指纹（git hash-object，我现场计算）

| 文件 | 我的 SHA-1 | 与反例审查记录（§1） |
|---|---|---|
| `app/agents/pipeline.py` | `192447b660ec4ea366dc27186c01cb74412f8e27` | ✓ 一致 |
| `app/core/config.py` | `8e9afa64c7968c220f7564bc6b881ac8eb80f4e8` | ✓ 一致 |
| `.env.example` | `cb6f269b7a793229b11462850025ca2aff971f2d` | ✓ 一致 |
| `tests/test_pipeline_budget.py`（新增） | `747c6b7f5cd2feae7303f116dc8454966cdd4f05` | ✓ 一致 |

docs 证据文件指纹（未跟踪，供后续核对）：plan=`69a20814…`、implementation=`98db653b…`、closure=`2b147b32…`。
diff --stat：.env.example 17 行变更（其中 RAG_IMPORT 段为 CRLF/LF 归一化噪音，内容未变，与反例审查 P3-3 一致）、pipeline.py +29/-1、config.py +12/-1——与实现说明 §1/§3 声明一致。

## 4. 实际复跑命令与退出码（项目解释器，隔离 basetemp，均我亲自执行）

| 命令 | 结果 | 退出码 |
|---|---|---|
| `pytest tests/test_pipeline_budget.py --basetemp=.../manual-review-pagt003-budget` | **17 passed** | 0 |
| `pytest -k "pipeline or budget" --basetemp=.../manual-review-pagt003-card` | **128 passed, 1621 deselected** | 0 |
| `pytest tests/test_tools.py tests/test_audit_smoke.py tests/test_pagt001_tools.py --basetemp=.../manual-review-pagt003-tools2` | **70 passed** | 0 |
| `pytest -k "tool or audit" --basetemp=.../manual-review-pagt003-toolaudit` | **153 passed, 1596 deselected** | 0 |
| `pytest tests/test_agent_chain.py --basetemp=.../manual-review-pagt003-agentchain2` | **41 passed** | 0 |
| 前任复验脚本 `data/pytest-tmp/manual_pagt003_verify.py` | **ALL_MANUAL_CASES_PASSED**（run() 截断+两请求隔离+QualityGate 评分稿无 notice+run_stream 事件=2） | 0 |
| 我自造脚本 `data/pytest-tmp/manual_review_pagt003_mine.py`（M1–M6 共 21 断言） | **ALL_MINE_CASES_PASSED** | 0 |
| `ruff check . --no-cache` | All checks passed | 0 |
| `mypy app/` | Success: no issues found in 195 source files | 0 |

数字与 `closure_pagt_003_20261011.md` §3 逐项一致；未触碰生产库 `data/ai_assistant.db` 与 `.env`。

## 5. 我自选的业务操作复验（自造脚本，不依赖前任/实现者 helper）

脚本 `data/pytest-tmp/manual_review_pagt003_mine.py`：自造 ScriptLLM（按 system 提示词分支）、自造 probe 工具，走真实 `AgentPipeline.run()`：

- **M1** 新管线实例默认 `max_tool_rounds==10`。
- **M2** Settings 校验：True/0/-1/101/2.5/"abc` 全拒；"10"→10、100 接受。
- **M3** run() 完整路径循环调用跑满 rounds=2 → 执行恰 2 次；draft 尾部确认为预算耗尽 notice；answer 回显证明 notice 经 `_build_respond` 进入「最终应答者」输入。
- **M4** QualityGate 开启（score=0.1、1 次重入）→ 评分提示不含 notice；最终 draft notice 恰 1 份；重入后总执行=4（每次重进行动循环重新获得预算——已登记边界）。
- **M5** 自愿停（1 次调用后给无 tool_call 草稿，rounds=5）→ 执行 1 次、**不带** notice（现状回归）。
- **M6** 两独立管线各自跑满 rounds=2 → 计数互不共享。

过程说明：首跑 M6 因我自身脚本笔误（act6a 调用了未注册的 "probe" 而非 "probeA"）报 1 项 FAIL；修正后 21/21 全绿。该失败系审核者 harness 笔误，**非实现缺陷**（未注册工具被管线拒绝、不执行、照耗预算，行为本身正确）。

## 6. 关闭对账记录核对结论

- closure §1 原始契约 9 行逐项与我 §1 核对一致；§2 五类反例用例名在 `tests/test_pipeline_budget.py` 中真实存在且全绿；§3 退出码表与我 §4 复跑一致；E2 标注名实相符（真实 AgentPipeline/`_iter_action_loop`/真实 ToolRegistry + 脚本化 LLM + 隔离 basetemp）。
- §4 未验证项原样保留 5 条：①真实 LLM 转述质量（脚本测试断言 draft 层）；②`fast_path.py:24,99` `_TOOL_ROUND_CAP=2` 绕开 settings（文件越界未改，短路径耗尽依赖既有 `_EXHAUSTED_DRAFT`，不带本卡新 notice）；③QualityGate 重入累计预算（默认关，已登记）；④多实例并发 E1；⑤PostgreSQL E1。无伪证据、无「未验证也关闭」。

## 7. DoD 核对（AGENTS.md §13）

- 验收可复现证据：§4 全部退出码可复跑。✓
- Diff 未超范围、Non-goals 未实现：§1 表两项 non_goal 均实证未越界。✓
- 既有行为回归：tools 基线 70、tool/audit 153、agent_chain 41 全绿；ruff/mypy 0。✓
- 文档同步：计划/实现说明/关闭对账/两轮审查均已落盘；tasks.yaml 按流程由主 Agent 验收后更新（当前未改，属预期）。✓
- 未验证项与风险：§6 原样记录。✓

## 8. 缺陷清单

### P0（阻断正确性）：无
### P1（违反契约/行为错误）：无
### P2（需修订方可通过）：无

### P3（观察项，不阻断，均已在既有文档登记）
- **P3-1**：`fast_path.py` 硬编码 `_TOOL_ROUND_CAP=2` 绕开 settings，且短路径不经 run/run_stream，`_apply_budget_notice` 不在该路径触发——短路径耗尽走既有 `_EXHAUSTED_DRAFT`（仍有耗尽说明语），acceptance② 实质成立。该文件在 allowed_paths 之外，建议后续工具治理卡复核短路径预算文案一致性。
- **P3-2**：本地 `.env:149` 仍 `AGENT_MAX_TOOL_ROUNDS=5`（forbidden 文件未改）；代码 Field 默认与 `.env.example` 均已对齐 10，属运维覆盖，非缺陷。
- **P3-3**：`.env.example` diff 中 RAG_IMPORT 段（142–148 行）内容未变但被标记为改动，判为 CRLF/LF 归一化噪音。
- **P3-4**：README 配置同步（5→10）不在 allowed_paths，未改；登记后续小卡跟进（AGENTS.md §11 配置同步义务）。

## 9. 结论与放行建议

**人工审核（第 1 轮）验收通过。** 必交付项均有 E2 隔离集成证据且经我独立复跑复核；独立业务复验 21/21 通过；回归全绿；无 P1/P2。本卡可由主 Agent 更新 tasks.yaml 状态并登记完成；P3 各项随后续治理卡跟进，不阻断本卡关闭。
