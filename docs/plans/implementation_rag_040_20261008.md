# RAG-040 版本绑定章节摘要 — 实现说明

> 日期：2026-10-08
> 卡片：`tasks.yaml` RAG-040（P1，L2）
> 计划：[plan_rag_040_20261008.md](plan_rag_040_20261008.md)
> 范围：仅 allowed_paths 内；未改 `tasks.yaml`；未 git 提交；未触碰 `.env`/生产数据/冻结基线；未触碰 RAG-015/032/036。

## 1. 功能实际完成了什么

为知识库「大章节」（父块）提供一条**可选、默认关闭、版本绑定**的派生摘要链路：

- **独立状态摘要任务**：`SectionSummaryJob`（pending/running/success/partial/failed），仿 `ImportJob` 状态机，记录 `calls_used`、`chapters_total/ready/failed`、`attempt_count`。
- **分批合并**：`ChapterSummarizer.summarize` 把超单批预算的章节正文按字符窗切批，逐批摘要，再发一次合并调用得到一条章节摘要。
- **支撑原文 ID**：每条 `SectionSummary.source_chunk_ids`（JSON）记录父块 ID 及其子块 ID，供原文回查。
- **版本绑定**：每条摘要记录 `document_id`、`source_version_hash`（= `Document.content_hash`）、`chunk_plan_version`（取自 `Document.chunk_plan` JSON 的 `version`，缺失记 `legacy`）、`model`、`prompt_version=section-summary-prompt-v0.1`、`protocol_version=section-summary-protocol-v0.1`。
- **失效语义**：`get_readable_summary` 复用 `can_read_document`——源文档软删（`deleted_at`）、被替换（`is_current=False`）、跨租户时读取立即抛 `PermissionError`；摘要行**不物理删除**（沿源文档保留政策）。
- **读时版本新鲜度（P2 修复后追加）**：读取时除授权外，还断言 `summary.source_version_hash == doc.content_hash` 且 `summary.chunk_plan_version == 当前 doc.chunk_plan 版本`，不一致即拒绝。这覆盖 `_reindex_document_in_place` 在同一 document row 原地改 `content_hash`（保持 `is_current=True`）的真实路径——旧 hash 绑定的摘要不得再被读出。
- **成本限制**：`ChapterSummarizer` 有 `max_calls` 计数器，耗尽即停；构造时可注入 `GenerationCapability`，每次发请求前经 `enforce_generation_budget`（RAG-028）。
- **默认关闭**：`RAG_SECTION_SUMMARY_ENABLED=False`。

## 2. 成功与失败行为

- 成功：父块正文非空、provider 返回非空文本 → `SectionSummary(status=ready)`，job 记 success/partial。
- 空章节：不发请求、不建行（数值边界）。
- LLM 抛错 / 超时 / 空输出 / 上下文预算超限 / 调用数耗尽：该章节记 `SectionSummary(status=failed, error=<原因码>)`，**原文块与检索路径完全不受影响**；job 记 success（无失败）/ partial（有成功有失败）/ failed（无成功）。
- 重入幂等：重跑同一文档时，按绑定键（document_id + chunk_id + source_version_hash + chunk_plan_version + model + prompt_version + status=ready）命中已 ready 摘要则跳过，不重复调用。

## 3. 代码位置

- 新表：`app/models/rag.py`（`SectionSummaryJobStatus`、`SectionSummaryStatus`、`SectionSummaryJob`、`SectionSummary`）。
- 迁移：`alembic/versions/c1d2e3f4a5b6_add_rag_section_summaries.py`（down_revision=`b2c3d4e5f607`）。
- 服务：`app/rag/section_summaries.py`（`ChapterSummarizer`、`create_summary_job`、`run_summary_job`、`get_readable_summary`）。
- 配置：`app/core/config.py`（`RAG_SECTION_SUMMARY_*` 五项，默认关闭）、`.env.example`、`README.md` 配置表。
- 评价骨架：`evals/section_summary/run.py` + `__init__.py`（合成 provider，离线可跑）。
- 测试：`tests/test_rag_040_section_summaries.py`（12 条，覆盖五类失败边界）。

## 4. 验证命令与结果

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。隔离配方：`PYTHONUTF8=1`、独立 `--basetemp`、清空四家 API key、独立 SQLite。

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest tests/test_rag_040_section_summaries.py -q --basetemp=...` | 0 | 12 passed（先红后绿：初跑 12 failed（模块不存在），实现后 12 passed） |
| `pytest tests/test_rag_040_section_summaries.py tests/eval/test_generation_budget.py tests/eval/test_generation_judge.py tests/eval/test_generation_eval_chain.py tests/test_rag_030_dialog_parent_assembly.py -q` | 0 | 50 passed（关联回归 RAG-028/029/030/035） |
| `ruff check <本卡改动文件>` | 0 | All checks passed |
| `mypy app/` | 0 | Success: no issues found in 187 source files |
| `python -m evals.section_summary.run` | 0 | 合成报告 JSON 产出，`real_calls_made=false`、`human_review_status=pending` |
| `alembic upgrade head`（scratch SQLite） | 0 | 干净升级到 `c1d2e3f4a5b6`，单 head |
| `pytest -q`（全量） | 1 | **1258 passed, 2 failed, 3 skipped** |

### 全量 2 条失败的取证结论（既有基线，非本卡引入）

1. `tests/test_rag_033_parse_quality.py::test_report_file_name_carries_gate_version` — 报告文件名带 gate 版本/日期，日期漂移导致断言不等（任务书已点名）。本卡未改 `test_rag_033` 或解析质量代码。
2. `tests/test_rag_log_minimization.py::test_milvus_delete_failure_retains_diagnostics_without_exception_body` — 测试 lambda mock 与 `app/rag/vectorstore/milvus.py:264` 调用签名（多了 `index` 关键字）不匹配（任务书已点名）。本卡未改 milvus.py。

两条失败的代码路径均不在本卡 allowed_paths 改动范围内，属于既有基线。`test_rag_028` 未出现共享库残留失败。

### P2-1 修复记录（独立审查 2026-10-09）

独立审查发现阻断 P2：`get_readable_summary` 只复用 `can_read_document`，未校验读时版本绑定新鲜度；`_reindex_document_in_place` 会在同一 document row 原地改 `content_hash` 且保持 `is_current=True`，旧 hash 绑定摘要仍被读出。

- **反例（先红后绿）**：`test_in_place_content_hash_change_rejects_old_summary`——同 row 把 `content_hash` 由 `hash-v1` 改 `hash-v2-reindexed`（is_current 仍 True、不软删、同租户），修复前 `DID NOT RAISE`（红），修复后抛 `PermissionError`，且摘要行未物理删除。
- **修复**：`get_readable_summary` 追加 `summary.source_version_hash == doc.content_hash` 与 `summary.chunk_plan_version == _chunk_plan_version(doc.chunk_plan)` 一致性断言，不一致即拒绝。
- **顺带补 O-4 回归**：`test_context_budget_guard_blocks_before_provider_call`——注入 `GenerationCapability(max_output_tokens=100)`，输出预留 256 > 100，RAG-028 预算 Guard 在发请求前拦截，`provider.calls==0`、章节 failed(`error=context_budget_exceeded`)。
- **复跑结果**：`pytest tests/test_rag_040_section_summaries.py -q` → **14 passed，退出码 0**；关联回归（RAG-022 chunking/routing/plan + RAG-028/029/030/035）→ **110 passed，退出码 0**；`ruff check` → 退出码 0；`mypy app/` → 退出码 0。
- **修复后指纹**：`app/rag/section_summaries.py` `5e3fae32aca0833…`；`tests/test_rag_040_section_summaries.py` `f4285c0c82c33f75…`。

## 5. 明确没做的事（Non-goals 落地）

- 未把摘要接进对话/检索主链路，未建摘要向量检索，未用摘要替代原文块。
- **`RAG_SECTION_SUMMARY_ENABLED` 开关当前无运行时读取方**（观察项 O-2）：「默认关闭」的实质是「模块未接入任何入口」，翻成 `true` 当前不改变行为。这是本卡 non-goal「不接入主链路」的预期结果，不是缺陷；后续接线（任务/读路径暴露）属另卡，不在本卡范围。

- 未把摘要接进对话/检索主链路，未建摘要向量检索，未用摘要替代原文块。
- 未做摘要物理清理/定时删除；源文档失效只拒绝读取，不删摘要行。
- **未发起任何真实模型/付费调用**；所有 LLM 路径用注入的合成 provider 验证。
- 未新增数据库直连、顶层架构或第三方依赖。
- 未改 `tasks.yaml`，未 git 提交，未触碰 RAG-015/032/036。

## 6. 依赖链如实说明

- RAG-022（切分计划版本，done）、RAG-028（预算 Guard，done）、RAG-029（selected/组装，done）已 done；本卡复用其 `Document.chunk_plan` 版本与 `enforce_generation_budget`。
- **RAG-035 未 done**：F1 P1 缺陷（rag-036 受限标识披露已登记未修复）与人工 Gold / 发布阈值待用户确认。本卡实现不依赖 RAG-035 关闭；仅复用其 `tests/eval/generation_judge.py` 作为「真实保真评价」骨架形状参考。**前置缺口未闭合，本卡不得据此写成 done。**

## 7. 关闭对账记录

证据类别：E1=真实入口+真实后端/模型；E2=真实代码+隔离后端（独立 SQLite/合成 provider）；E3=单元/自检；E4=推导/Mock/历史。必交付项仅接受 E1/E2。

| 原始契约项 | 证据类别 | 结果 | 证据位置 / sha256 |
|---|---|---|---|
| 独立状态摘要任务 | E2 | 通过 | `app/rag/section_summaries.py` `5e3fae32…`（P2 修复后）；测试 `test_create_and_run_job_produces_bound_summary` |
| 分批合并 | E2 | 通过 | `test_oversized_chapter_is_batched`（2 批+合并=3 次调用） |
| 支撑原文 ID | E2 | 通过 | `test_source_chunk_ids_point_to_real_parent`（父+子 ID 回查） |
| 版本 hash/Prompt/模型绑定（写） | E2 | 通过 | `test_create_and_run_job_produces_bound_summary` 断言 hash/chunk_plan/model/prompt_version |
| 失效（软删/非当前/跨租户/原地 hash 变更）拒绝 | E2 | 通过（P2 已修复） | `test_cross_tenant_read_rejected`、`test_soft_deleted_doc_summary_rejected`、`test_replaced_non_current_doc_summary_rejected`、`test_in_place_content_hash_change_rejects_old_summary` |
| 版本绑定读时新鲜度校验 | E2 | 通过（P2 修复后） | `get_readable_summary` 校验 `source_version_hash`/`chunk_plan_version`；反例见上 |
| 成本限制（所有调用受预算） | E2 | 通过 | `test_max_calls_exhausted_stops_calling`（≤max_calls）；`test_context_budget_guard_blocks_before_provider_call`（发请求前拦截、零调用） |
| 失败不影响原文检索 | E2 | 通过 | `test_provider_failure_marks_failed_and_keeps_original_chunks`（原文块保留） |
| 中断重入幂等 | E2 | 通过 | `test_rerun_is_idempotent_when_ready_summary_matches_binding` |
| 不替代原文/不另建摘要向量检索/不默认启用 | E2 | 通过 | `test_default_off_config_flag_exists`（开关默认 False）；摘要不进检索路径 |
| 原文回查 | E2 | 通过 | 同「支撑原文 ID」 |
| **真实保真评价** | — | **未验证（无调用授权）** | `evals/section_summary/run.py` `caa60514…` 仅合成链路；人工 Gold/真实保真待授权，`human_review_status=pending` |
| 存储细化与费用/质量门槛落盘 | E3/文档 | 通过（提案） | 计划第 7 节；数值为护栏提案，未经真实费用测量 |
| 迁移 | E2 | 通过 | `c1d2e3f4a5b6…`，alembic upgrade head 退出码 0 |
| 配置/.env.example/README 同步 | E2 | 通过 | `config.py 219b588b…`、`.env.example 3566a2e0…`、`README.md d71a29ac…` |

### 未验证项（原样保留，不伪装为完成）

1. **真实保真评价未验证**：无真实模型/费用调用授权，仅交付可运行骨架与报告 schema；摘要对原文的事实覆盖/幻觉/引用准确性需人工 Gold 复核，待授权后另卡执行。
2. **RAG-035 未关闭**：其 P1 缺陷与人工确认项待用户；本卡不依赖其关闭，也不据此声明前置缺口已闭合。
3. 费用/质量门槛为护栏提案：`max_calls=8`、单批 4000 字符、输出 256 token 未经真实成本测量，不得写成达标结论。
