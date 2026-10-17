# PAGT-003 入口级反例审查结论

- 审查对象：PAGT-003「工具调用预算与循环上限」最终实现（分支 `codex/pagt-tool-polish`，未提交改动）
- 审查角色：独立入口级反例审查 Agent（未参与计划与实现编写）
- 审查日期：2026-10-12
- 审查基准：`tasks.yaml` PAGT-003（4673–4718）、AGENTS.md §7/§9/§10
- 关联文档：`docs/plans/plan_pagt_003_20261011.md`、`implementation_pagt_003.md`、`closure_pagt_003_20261011.md`、`docs/reviews/2026-10-11-PAGT-003-plan-review.md`
- 边界：只读核对 + 隔离反例运行；未改业务代码/测试/docs-plans 三文档/tasks.yaml；未做 git 提交。反例文件 `data/pytest-tmp/ce_pagt003.py`（审查临时产物）。

---

## 0. 总体结论：通过

无 P0、无 P1、无 P2 阻断缺陷。实现与契约、计划（P2-1 方案 B）一致；for…else 三路径区分、QualityGate 开/关两路径、配置边界、每请求隔离、回滚幂等均经**独立构造反例实测**通过；回归全绿且数字与实现者声明一致。fast_path 短路径 notice 缺口属 allowed_paths 之外的已登记边界（非阻断）。

---

## 1. 代码指纹（git hash-object，未提交工作区）

| 文件 | 指纹（SHA-1） | 与 diff index |
|---|---|---|
| `app/agents/pipeline.py` | `192447b660ec4ea366dc27186c01cb74412f8e27` | 96f52d4..192447b ✓ |
| `app/core/config.py` | `8e9afa64c7968c220f7564bc6b881ac8eb80f4e8` | 7573605..8e9afa6 ✓ |
| `.env.example` | `cb6f269b7a793229b11462850025ca2aff971f2d` | 3b1a2c0..cb6f269 ✓ |
| `tests/test_pipeline_budget.py`（新增） | `747c6b7f5cd2feae7303f116dc8454966cdd4f05` | 新文件 ✓ |

范围核对：diff 改动全部落在 allowed_paths（pipeline.py / config.py / .env.example / tests/）。**未改** fast_path.py、chat_service.py、tools/、audit/、api/、tasks.yaml、任何既有测试文件。

---

## 2. 回归验证（实际运行，项目解释器，隔离 basetemp）

| 命令 | 结果 | 退出码 |
|---|---|---|
| `pytest -k "pipeline or budget"`（卡命令） | **128 passed, 1621 deselected** | 0 |
| `pytest -k "tool or audit"`（PAGT-002 回归） | **153 passed, 1596 deselected** | 0 |
| `pytest tests/test_agent_chain.py`（含既有 exhausted trace 路径 F14） | **41 passed** | 0 |
| `pytest tests/test_tools.py tests/test_audit_smoke.py tests/test_pagt001_tools.py`（tools 基线） | **70 passed** | 0 |
| `ruff check . --no-cache` | All checks passed | 0 |

数字与 `closure_pagt_003_20261011.md` §3 声明一致。

---

## 3. 独立反例清单（入口 / 操作 / 期望 / 实际）

独立于实现者的 17 用例，另构造 8 条反例探查未覆盖面（流式、畸形格式、短路路径、幂等）：

| # | 入口 | 操作 | 期望 | 实际 | 结果 |
|---|---|---|---|---|---|
| CE-1 | run_stream 流式完整路径 | 脚本 LLM 永远发递增参数 echo，rounds=2，消费流式事件 | 恰执行 2 次、draft notice 恰一份 | counter=2、draft notice=1 | ✅ |
| CE-2 | run() 失败路径 | LLM 反复发含 `<tool_call>` 但 JSON 非法的畸形串，rounds=2 | 0 次成功执行、记录 invalid 观测、跑满带 notice | counter=0、有「调用格式无法解析」、draft 含 notice | ✅ |
| CE-3 | run() preflight 短路（NO）路径 | 短路策略 `_SHORT_PATH_TOOLS={calculator,code_sandbox,get_current_datetime}` 不含 echo，echo 被拒，rounds=2 | echo 被 denied、被拒轮次耗满预算、draft 仍带 notice | counter=0、有「不允许调用该工具」、draft 含 notice | ✅ |
| CE-4 | 配置边界 | `Settings.model_fields[...].default`；`"10"` 字符串；`100`；`101` | 默认=10；"10"→10；100 接受；101 拒 | 全部符合 | ✅ |
| CE-5 | `_apply_budget_notice` 幂等 | 手工置 `tool_budget_exhausted=True`，连调两次 | draft notice 仍恰一份 | notice=1 | ✅ |
| CE-6 | run() 重复 break | 同参数 echo 连发，rounds=5 | 执行 1 次、**不带 notice**（防误报） | counter=1、无 notice | ✅ |
| CE-7 | run() 自愿停 | 调 2 次后 LLM 给收尾草稿，rounds=5 | 执行 2 次、**不带 notice**（现状回归） | counter=2、无 notice | ✅ |
| CE-8 | run() QualityGate 开启（P2-1 方案 B） | `AGENT_QUALITY_GATE_ENABLED=true`、score=0.1、rounds=2 | 首循环 2 次+重入 2 次=4 次；被评分稿**不含** notice；最终 draft notice 恰一份 | counter=4、评分稿无 notice、最终 notice=1 | ✅ |

反例运行记录：首跑 8 项中 2 项失败（CE-3 初版断言 `counter==2`、CE-4 初版断言 `Settings()==10`），经核查均为**审查者反例前提错误**，非实现缺陷：

- CE-4：`Settings()` 读取本地 `.env:149` 的 `AGENT_MAX_TOOL_ROUNDS=5`（forbidden 文件，实现者正确未改）；代码级 `Field(default=10)` 才是隔离默认值。修正断言为 `model_fields.default==10` 后通过。
- CE-3：短路策略白名单不含 echo，echo 被 denied（非执行）。修正断言为 `counter==0` + denied 观测 + 仍带 notice 后通过。

修正后 8/8 全部通过（`pytest data/pytest-tmp/ce_pagt003.py`，退出码 0）。

---

## 4. 关键机制复核（对照审查维度）

1. **for…else 区分真实性**：`_iter_action_loop` 的 `else` 仅在循环跑满全部轮次（非 `return`/`break`）时置 `tool_budget_exhausted=True`。独立实测：自愿停（CE-7）、重复 break（CE-6）均不带 notice；跑满才带（CE-1/CE-2）。既有 `test_trace_records_tool_statuses_without_secrets`（同参重复→skip break→tail finalize→trace 含 exhausted）回归通过，**无误报泄漏**（agent_chain 41 passed 含此用例）。
2. **P2-1 方案 B（QualityGate 开/关）**：notice 注入点在 `_quality_gate_loop` 之后、`_build_respond` 之前（run 短路+完整、run_stream 短路+完整共 4 处）。CE-8 实测开启时被评分稿不含 notice、最终 draft 仅一份 notice；关闭路径正常。
3. **配置边界**：0/-1/True/2.5/abc/101 拒绝（实现者用例 + CE-4）；默认 10；合法字符串接受；与 `RAG_IMPORT_MAX_CONCURRENCY` 校验范式一致。
4. **每请求/并发隔离**：预算 = `_iter_action_loop` 局部轮次，AgentState/管线每请求新建；两独立请求计数互不共享（实现者 `test_budget_is_per_request_not_global` + CE-6/CE-7 各自独立）。无全局共享计数。
5. **失败/被拒耗预算**：抛错工具（实现者用例）与畸形格式（CE-2）、短路 denied（CE-3）均照常耗预算、不因异常提前/延后停。
6. **回滚幂等**：跑满后无第 N+1 次调用、无额外副作用（实现者 `test_at_budget_no_extra_side_effects`）；`_apply_budget_notice` 幂等（CE-5）。
7. **关闭对账抽查**：closure 引用的用例名真实存在且通过；E2 标注名实相符（真实 AgentPipeline/_iter_action_loop/真实 ToolRegistry + 脚本化 LLM + 隔离 basetemp）；未验证项（真实 LLM 转述、fast_path 旁路、QualityGate 重入累计、多实例 E1、PostgreSQL E1）原样列出。

---

## 5. 缺陷清单

### P0（阻断正确性）：无
### P1（违反契约/行为错误）：无
### P2（需修订方可通过）：无

### P3（观察项，不阻断）

- **P3-1（已登记边界）**：`fast_path.py:24,99` 硬编码 `_TOOL_ROUND_CAP=2` 绕开 settings，且 fast_path 直接调 `_iter_action_loop`、不经 run/run_stream，故 `_apply_budget_notice` 不在短路径触发——短路径耗尽走既有 `_EXHAUSTED_DRAFT`，**不带本卡新 notice**。该文件在 allowed_paths 之外，本卡不能修；实现 §5① 与 closure §4 已如实登记。原计划 §2.4「文案改动对短路径同样生效」的乐观表述已被实现纠正。短路径耗尽仍有既有耗尽说明语，acceptance② 实质成立。
- **P3-2（本地环境，非缺陷）**：本地 `.env:149` 仍 `AGENT_MAX_TOOL_ROUNDS=5`（forbidden，未改）。代码 Field 默认与 `.env.example` 均已对齐 10；生产/全新部署按默认 10 生效，本地 `.env` 属运维覆盖。
- **P3-3（无关噪音）**：`.env.example` diff 中 RAG_IMPORT 段（约 142–148 行）内容未变但被标记为改动，判为行尾（CRLF/LF）归一化噪音，与本卡无关。
- **P3-4（后续跟进）**：README 配置同步（5→10）不在 allowed_paths，未改；closure 已登记为后续小卡。

---

## 6. 结论与放行建议

必交付项均有 E2 隔离集成证据，独立反例 8/8 通过，回归全绿（卡片命令 128、tool/audit 153、agent_chain 41、tools 基线 70、ruff 0）。**入口级反例审查通过**，无阻断缺陷。P3 各项为已登记边界或观察项，不阻断本卡；其中 P3-1（fast_path notice 缺口）应在后续工具治理卡（可挂 PWFL/PAGT 族）中复核短路径预算文案一致性。可进入人工审核环节。
