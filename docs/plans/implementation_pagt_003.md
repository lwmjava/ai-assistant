# PAGT-003 实现说明：工具调用预算与循环上限

- 卡编号：PAGT-003
- 分支：codex/pagt-tool-polish
- 实现日期：2026-10-12
- 前置：PAGT-002（done，HEAD 5c49368，开工前已复核）
- 计划：`docs/plans/plan_pagt_003_20261011.md`（按计划审查 P2-1 修订为方案 B）
- 审查：`docs/reviews/2026-10-11-PAGT-003-plan-review.md`（结论「需修订」，无 P0/P1；P2-1 已按方案 B 修订并实现）

## 1. 实际完成了什么

单次请求的工具行动轮次达到配置上限后停止工具行动，并在最终回复草稿中确定性说明「因预算耗尽而停止」；上限对齐 PRD §5.1.3 的 10 并配置化、带取值校验。不改 PAGT-002 的工具门控/审计、不改 QualityGate 评分逻辑。

### 1.1 管线循环层（改 `app/agents/pipeline.py`，+29/-1）

- 新常量 `_BUDGET_EXHAUSTED_NOTICE`（pipeline.py:57-60）：`（系统提示：本次对话已达到工具调用次数上限 {limit} 次，后续内容基于已有工具结果生成。）`
- `AgentState` 新增 `tool_budget_exhausted: bool = False`（请求态标志）。
- `AgentPipeline.__init__` 默认 `max_tool_rounds: int = 10`（对齐 PRD；chat_service 两处仍显式从 settings 赋值）。
- `_iter_action_loop` 的 `for` 循环加 `else: state.tool_budget_exhausted = True`：仅当循环跑满全部轮次（`return`/`break` 不触发）时置标志；循环内不改 draft。既有尾块 `if wrap_up and …: _finalize_without_tools` 原样保留——finalize 只经此一处，天然幂等、不 double-finalize（审查 P3-1）。
- 新方法 `_apply_budget_notice(state)`：标志未置或 draft 已含后缀则不动（幂等）；否则把 notice 追加到 `state.draft`。注入点刻意放在 QualityGate 之后（审查 P2-1 方案 B），共四处调用：`run()` 短路路径行动循环后、`run()` 质量门循环后、`run_stream()` 短路路径行动循环后、`run_stream()` 质量门块后。

### 1.2 配置（改 `app/core/config.py`，+12/-1）

- `AGENT_MAX_TOOL_ROUNDS: int = Field(default=10, ge=1, le=100)`（默认对齐 PRD §5.1.3 的 10）。
- 新增 `@field_validator("AGENT_MAX_TOOL_ROUNDS", mode="before")`：bool 或非 int/str → `ValueError("单次对话工具调用上限必须是 1..100 的整数")`，对齐既有 RAG_IMPORT 校验范式（config.py:432 旁）。

### 1.3 `.env.example`（+5/-3）

- `AGENT_MAX_TOOL_ROUNDS=5` → `=10`，注释补「范围 1~100；达到上限后停止并在最终回复中说明」。

## 2. 谁可以做什么 / 成功与失败行为

| 行为 | 成功 | 失败 |
|---|---|---|
| LLM 正常调工具后给答案（未达上限） | 正常草稿，不带 notice（现状不变） | — |
| LLM 循环调用工具跑满 N 轮 | 第 N+1 次不再发起；draft 末尾追加预算耗尽说明 | LLM 在 finalize 后仍坚持工具调用 → 既有 `_EXHAUSTED_DRAFT` 与 notice 并存 |
| 工具抛错/超时 | 错误观测入 tool_results，照常消耗预算，循环继续直到截断 | 不因工具异常提前或延后停 |
| 同参数重复调用 | 第 2 次 skip break；**不带 notice**（非预算耗尽，防误报） | — |
| 配置 `AGENT_MAX_TOOL_ROUNDS=0/-1/True/2.5/非数值/101` | — | 启动装载即 ValidationError（拒绝启动） |
| QualityGate 开启 | 被评分 draft 不含 notice；最终 draft 仍带 notice 且仅一份 | — |

## 3. 代码位置

- `app/agents/pipeline.py`：常量（:57-60）、AgentState 字段、`__init__` 默认 10、`_apply_budget_notice`、循环 `for…else`、四处注入点。
- `app/core/config.py`：`AGENT_MAX_TOOL_ROUNDS` Field + before 校验器。
- `.env.example:223-226`。
- `tests/test_pipeline_budget.py`（新增 17 用例）。

## 4. 验证命令与结果（项目解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`）

| 命令 | 退出码 | 结果 |
|---|---|---|
| 前置复核 `pytest -k "tool or audit" --basetemp=.../pagt003-plan-baseline` | 0 | 152 passed（PAGT-002 基线） |
| Red：`pytest tests/test_pipeline_budget.py --basetemp=.../pagt003-red1` | 1 | 10 failed / 7 passed（先写反例） |
| Green：`pytest tests/test_pipeline_budget.py --basetemp=.../pagt003-green2` | 0 | 17 passed |
| 卡命令 `pytest -k "pipeline or budget" --basetemp=.../pagt003-run1` | 0 | 128 passed, 1621 deselected |
| 回归 `pytest -k "tool or audit" --basetemp=.../pagt003-regress` | 0 | 153 passed, 1596 deselected |
| tools 基线 `pytest tests/test_tools.py tests/test_audit_smoke.py tests/test_pagt001_tools.py --basetemp=.../pagt003-tools-base` | 0 | 70 passed |
| `ruff check . --no-cache` | 0 | All checks passed |
| `mypy app/` | 0 | Success: no issues found in 195 source files |

全部 pytest 使用隔离 `--basetemp=data/pytest-tmp/<唯一目录>`；未触碰生产库 `data/ai_assistant.db`。

## 5. 审查缺陷与决策登记

- **P2-1（审查）→ 方案 B 已采纳**：notice 注入点后移到 QualityGate 循环之后、`_build_respond` 之前，被评分 draft 不含 notice。证据：`test_quality_gate_on_scored_draft_excludes_notice` 断言质量评估员提示不含 notice、最终 draft 仅一份。
- **P3-1（审查）**：新 budget finalize 与既有尾块功能重叠——实现上 finalize 只保留既有尾块一处，本卡只加标志位与事后注入，不 double-finalize；`_apply_budget_notice` 幂等（已含即不追加）。
- **P3-3（审查）**：README 配置同步缺口（`AGENT_MAX_TOOL_ROUNDS=5→10`）——README 不在本卡 allowed_paths，未改；登记为后续小卡跟进，不在本卡扩范围。
- **既有偏差登记**：①`fast_path.py:24,99` 硬编码 `_TOOL_ROUND_CAP=2` 绕过 settings（文件越界未改），该路径耗尽文案依赖既有 `_EXHAUSTED_DRAFT`，不追加本卡 notice；②QualityGate 开启时每次重进行动循环重新获得一轮预算（本卡不做跨循环累计，默认关）；③`AGENT_MAX_TOOL_ROUNDS` 命名沿用既有 env 名（PRD 名 max_tool_calls），单轮≤1 次调用语义等价，不改名避免破坏性配置变更。

## 6. 明确没做的事（Non-goals）

- 不做跨请求/用户/租户全局配额（QUOTA-001）。
- 不改 QualityGate 评分逻辑、阈值与重入结构。
- 不改 `fast_path.py`、`chat_service.py`、`app/agents/tools/`、`app/audit/`、`app/api/`（越界）。
- 不改 README、frontend。
- 不做跨 QualityGate 重入的累计预算。
- 未改 tasks.yaml、未做 git 提交。

## 7. 回滚方式

git revert 本卡提交（.env.example 一行、pipeline.py 约 29 行、config.py 约 12 行、新增测试文件）；无 DB 迁移、无数据变更、无 schema 改动。
