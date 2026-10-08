# RAG-030 对话父块受控组装 — 实现说明（2026-10-09）

本说明对应 `docs/plans/plan_rag_030_20261008.md`。本卡把 RAG-016 已在**搜索 API**
展开并复核父块的能力，受控地接入**对话 / Agent 检索路径**（`HybridRetriever`），使送模型
的实际上下文在预算内带父块、去重、保留定位元信息，且他租户 / 历史 / 注入父块被拒绝。

## 1. 功能实际完成了什么

此前对话路径 `HybridRetriever.retrieve()` 只返回阈值过滤后的**子块**：
`RAGService.make_retriever()` 产出的检索器不绑定任何父块展开回调，`last_hits` 里没有父块，
模型实际上下文也没有父块。搜索 API 的 `_expand_parent_chunks`（授权 / 版本 / 注入复核 + 去重 +
定位元信息）没有复用到对话路径。

本次最小改动：

1. **`HybridRetriever` 新增可选 `parent_expander` 回调**（`app/rag/retriever.py`）：
   协议 `async def expander(hits, *, query="") -> list[ChunkResult]`。默认 `None` = 旧读路径。
2. **阈值过滤后受控展开**：`retrieve()` 在 `kept` 之后、`_settle` 之前调用新方法
   `_expand_parents(kept, search_text)`。父块只在子块通过 `RAG_MIN_SIMILARITY` 后由该子块触发，
   继承子块 similarity，不单独评分 / 进候选，因此**不可能越阈**。
3. **失败回退**：expander 抛异常时记 warning 并**回退到原 `kept`**（已通过 SQL 过滤与阈值的
   安全子块），不向上炸、不丢证据；后端不可用（`retrieve` 抛错收敛 unavailable）时 `kept` 为空，
   不调 expander。
4. **`RAGService.make_retriever()` 绑定 expander**（`app/rag/service.py`）：把 `_expand_parent_chunks`
   包成闭包（绑定 `index_id`，与搜索读路径同源）。对话检索路径**始终展开父块**，与 RAG-016
   搜索 API 既有行为一致；本卡范围裁决后不再保留独立配置开关（见 §8）。
5. **Context Builder 不改动**：父块进 `last_hits` 后，既有 `_select_rag_blocks` 按
   `RAG_CONTEXT_CHARS` 整块装入 / 整块丢弃，`last_selected` ↔ `[资料 N]` ↔ `_reply_sources`
   三方对齐、围栏闭合、预算硬护栏自然成立。

## 2. 同步 / 流式成功与失败行为

| 场景 | 成功行为 | 失败 / 降级行为 |
|---|---|---|
| 子块命中且父块可见（同文档 / 同租户 / 当前版 / 未注入） | `last_hits` = [子块, 父块, …]；父块 `chunk_kind=parent`、`retrieval_origin=parent_expansion`、`expanded_from_chunk_id` / `score_inherited_from_chunk_id` 指向触发子块；子块 `parent_id` 回填 | — |
| 多子块指向同一父块 | 父块在 `last_hits` 中只出现一次（`seen` 去重） | — |
| 父块超预算 | 父块在 `last_hits` 但不进 `last_selected`（整块丢弃，记 `REASON_BUDGET`），子块仍选入，不截断、不补位 | — |
| 父块不可见（他文档 / 他租户 / 非当前版 / 已软删 / 注入） | 安全子块保留，父块不进 `last_hits`、不进 payload、不进来源 | 安全子块照常返回 |
| expander 回查失败（DB 异常等） | — | 回退为子块 only；记 warning；status 仍按阈值 classify（ok / below_threshold），不冒充「无命中」 |
| 后端不可用（嵌入 / 向量库异常） | — | `kept=[]`，不调 expander；收敛 unavailable，不注入任何资料 |
| 未绑定 expander 的协议级调用方 | — | `parent_expander=None` 时 `HybridRetriever` 退化为子块 only（`test_retriever_without_expander_keeps_child_only` 守护） |

同步（`AgentPipeline.run`）与流式（`run_stream`）、Fast RAG 短路径、Supervisor 三条编排路径
**共用同一个 `HybridRetriever.retrieve()`**，因此展开行为对四条入口同源生效，无需分别接线。

## 3. 代码位置（改动文件，均在 RAG-030 allowed_paths）

| 文件 | 改动 |
|---|---|
| `app/rag/retriever.py` | 新增 `ParentExpander` Protocol；`HybridRetriever.__init__` 新增 `parent_expander` 参数；`retrieve()` 阈值过滤后调用 `_expand_parents`；`outcome.hits` 用展开结果，`status` 仍按阈值；新增 `_expand_parents`（None/空命中短路、异常回退子块）。 |
| `app/rag/service.py` | `make_retriever()` **始终**绑定 `_dialog_parent_expander(index_id)` 闭包（复用 `_expand_parent_chunks`）；对话路径默认展开父块，与 RAG-016 搜索路径一致。 |
| `tests/test_rag_030_dialog_parent_assembly.py` | 12 条：5 条接线层（假 backend + 假 expander）+ 7 条集成层（真实 RAGService + 本地 SQLite + MockEmbedding），覆盖五类失败边界；其中协议级用例守护「无 expander 时子块 only」。 |
| `docs/plans/plan_rag_030_20261008.md` | 本卡实施计划（含失败边界清单）。 |

未改：检索排名 / RRF / 阈值 / 检索公式（RAG-005 基线）、Context Builder 预算算法、Milvus、
`ChatService._reply_sources`、`app/agents/*` 编排路径、`app/core/config.py`（见 §8 范围裁决）。

## 4. 验证命令与结果（解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`）

隔离配方：独立 `DATABASE_URL=sqlite:///./data/pytest-tmp/<uuid>.db` + 独立 `--basetemp` +
清空 `LLM_API_KEY/EMBEDDING_API_KEY/DASHSCOPE_API_KEY/OPENAI_API_KEY` + `PYTHONUTF8=1`。

| 项 | 命令 | 结果 / 退出码 |
|---|---|---|
| 复现失败（改前） | `pytest tests/test_rag_030_dialog_parent_assembly.py -k "calls_parent_expander or resets_last_hits"` | 2 failed（expander 从未被调用）/ 1 |
| 修复后专项 | `pytest tests/test_rag_030_dialog_parent_assembly.py -v` | **12 passed** / 0 |
| 关联回归 | 该文件 + `test_rag_029_pipeline_alignment.py` + `test_rag_029_context_builder.py` + `test_rag_safe_parent_expansion.py` + `test_rag_026_uploader_scope.py` + `test_rag_037_retrieval_status.py` + `test_rag_threshold.py` | **138 passed in 8.40s** / 0（范围裁决收回 config.py 后重跑） |
| ruff | `python -m ruff check app/` | `All checks passed!` / 0 |
| mypy | `python -m mypy app/` | `Success: no issues found in 185 source files` / 0 |
| 全量 | `pytest -q`（隔离配方） | `2 failed, 1198 passed, 3 skipped in 280.83s` / 1（两条失败见 §7，SUT 不在本卡 diff；通过数较 RAG-029 基线 1186 +12 = 本卡新增用例；范围裁决前后全量通过数均为 1198，无新增回归） |

## 5. 明确没做的事（non-goals / 边界）

- 不把对话改成「只返回父块」——子块始终保留。
- 不改预算算法 / 阈值 / RRF k / 检索公式；不给 Context Builder 新增数据库直连。
- 不新建独立 Reranker / Query Rewrite；不改 Milvus；未碰 RAG-015 / RAG-032 / RAG-036 代码与状态。
- 未做真实 Embedding / LLM 调用；未重建索引；未生产启用真实模型（Mock 只证明链路，不证明检索质量）。
- **不新增独立配置开关**（范围裁决，见 §8）：对话父块展开默认开启，不提供运行时 env 关闭项；
  回滚方式为回退本卡提交。
- 未修改 `tasks.yaml`、未 git 提交（关闭登记由主 Agent 负责）。

## 6. 失败边界清单落实（五类，每类至少一反例测试）

| 类别 | 反例测试 | 结果 |
|---|---|---|
| 数值边界 | `test_parent_over_budget_not_selected_but_child_kept`（极小预算父块整块丢弃、子块保留）、`test_empty_hits_skip_expansion` | 通过 |
| 失败路径 | `test_parent_expansion_failure_falls_back_to_child_hits`、`test_backend_unavailable_skips_expansion`、同步 / 流式同一入口（四条编排共用 `HybridRetriever.retrieve`） | 通过 |
| 权限与租户 | `test_dialog_cross_tenant_parent_rejected`、`test_dialog_historical_parent_rejected`（uploader 范围由 `_expand_parent_chunks` 既有 uploader 过滤承担，复用 RAG-026 证据） | 通过 |
| 身份与绑定 | `test_dialog_retrieval_expands_parent_into_last_hits`（parent_id / chunk_kind / retrieval_origin / expanded_from / score_inherited / version_status） | 通过 |
| 回滚与幂等 | `test_dialog_parent_expansion_dedupes_shared_parent`、`test_retriever_without_expander_keeps_child_only`（无 expander 协议级子块 only）、`test_retrieve_resets_last_hits_between_turns` | 通过 |

## 7. 全量两条失败的取证（与本卡改动无关）

本卡业务 diff 仅 `app/rag/retriever.py`、`app/rag/service.py`（另加测试与 docs；
`app/core/config.py` 已按范围裁决还原，见 §8）。
两条失败的 SUT 均不在 diff 内：

| 失败测试 | 根因取证 | 与本卡关系 |
|---|---|---|
| `tests/test_rag_033_parse_quality.py::test_report_file_name_carries_gate_version` | 断言落盘提交报告名 `parse-quality-20261009-gate-v3.json` 等于 `default_report_name(datetime(2026,10,7))`（硬编码 20261007）。日期漂移：提交态报告文件名带当日日期，测试按硬编码日比对。 | RAG-033 报告命名，本卡未触碰 parse quality 代码；纯日期敏感的既有 artifact 漂移（与 RAG-029 记录的同一类）。 |
| `tests/test_rag_log_minimization.py::test_milvus_delete_failure_retains_diagnostics_without_exception_body` | `app/rag/vectorstore/milvus.py:264` 调 `self._connect(identity, index=target_index)`，而测试 monkeypatch 的 `_connect` lambda 不接受 `index=` 形参 → `TypeError`。 | Milvus 删除路径的 SUT / 测试 mock 契约不匹配，本卡未触碰 milvus.py（与 RAG-029 记录的同一类）。 |

## 8. 范围裁决记录（收回 allowed_paths 之外的改动）

实施过程中曾在 `app/core/config.py` 新增 `RAG_DIALOG_PARENT_EXPANSION: bool = True` 作为回滚开关。
**裁决**：`app/core/config.py` 不在 RAG-030 allowed_paths 白名单
（`app/rag/`、`app/services/chat_service.py`、`app/agents/`、`tests/`、`docs/`、`tasks.yaml` 只读），
向用户请求扩展许可未获答复（超时）。按治理保守原则执行**严格收回**：

1. `git checkout -- app/core/config.py` 还原到 HEAD，`git diff app/core/config.py` 为空 diff。
2. `app/rag/service.py` 的 `make_retriever()` 不再读任何配置开关，**始终**绑定父块展开闭包，
   与 RAG-016 搜索路径始终展开的既有行为一致，也满足本卡 acceptance「同步/流式实际上下文有父块」。
3. 删除依赖配置开关的集成用例，改为协议级用例 `test_retriever_without_expander_keeps_child_only`
   （直接构造 `HybridRetriever`、不读任何配置，断言 `parent_expander is None` 时退化为子块 only）。

**回滚方式**：本卡不再提供运行时独立配置开关；回滚 = 回退本卡提交（`app/rag/retriever.py` +
`app/rag/service.py` 两文件）。若后续用户批准，可再补 env 开关（届时需同步 `.env.example` 与 README）。
`HybridRetriever.parent_expander` 参数本身保留（默认 None 的子块 only 协议路径仍有测试覆盖）。

## 9. 关闭对账记录

证据类别：**E1**=外部运行证据；**E2**=隔离集成证据（真实代码 + 隔离后端）；
E3=自检 / 单元；E4=推导 / Mock / 历史。必交付项只认 E1/E2；E3/E4 一律记「未验证」。

| 原始契约（RAG-030） | 结果 | 证据类别 | 证据位置与文件指纹（SHA256） |
|---|---|---|---|
| 授权 / 版本 / 注入复核后按预算选入父块 | 通过 | E2 | expander 复用 `_expand_parent_chunks`（授权 / 版本 / 注入复核）；`build_context` 按预算整块选入。`app/rag/retriever.py` `7CFE56FD…95E3DC`、`app/rag/service.py` `60633D36…5B69BB3` |
| 去重 | 通过 | E2 | `test_dialog_parent_expansion_dedupes_shared_parent`（5 子块同一父块 → 父块出现一次）；`tests/test_rag_030_dialog_parent_assembly.py` `BE859716…EFA403` |
| 保留定位元信息（来源范围 / 块 ID / parent_id） | 通过 | E2 | `test_dialog_retrieval_expands_parent_into_last_hits`（chunk_kind / retrieval_origin / expanded_from / score_inherited / 子块 parent_id 回填）；来源经 `sources_from_hits(session, last_selected)` 带 chunk_id / document_id |
| 同步实际上下文有父块 | 通过 | E2 | `test_dialog_retrieval_expands_parent_into_last_hits`（make_retriever 真实 retrieve 后 last_hits 含父块） |
| 流式实际上下文有父块 | **未验证** | E4 | 同步 / 流式共用 `HybridRetriever.retrieve()`（同源），流式入口未单独跑真实 LLM 端到端；本卡未启动真实模型。属「从同源推导」，按门禁记未验证。 |
| 他租户父块拒绝 | 通过 | E2 | `test_dialog_cross_tenant_parent_rejected`（他文档父块不展开、不泄漏正文） |
| uploader 范围对父块生效 | 通过（既有复用） | E2 | `_expand_parent_chunks` 的 uploader 过滤（`read_scope.uploader_id`）复用 RAG-026 / RAG-016 证据；`test_rag_026_uploader_scope.py` 在 138 passed 回归内 |
| 历史 / 过期版本父块拒绝 | 通过 | E2 | `test_dialog_historical_parent_rejected`（非当前版文档父块不出现、不泄漏） |
| 注入父块拒绝 | 通过 | E2 | `test_dialog_injected_parent_dropped_child_kept`（注入父块剔除、安全子块保留） |
| 不绕过预算与阈值 | 通过 | E2 | `test_parent_over_budget_not_selected_but_child_kept`（父块超预算不进 selected）；父块继承子块 similarity，子块已过阈值 |
| expander 失败回退（失败路径） | 通过 | E2 | `test_parent_expansion_failure_falls_back_to_child_hits`、`test_backend_unavailable_skips_expansion` |
| 无 expander 协议级子块 only（回滚） | 通过 | E2 | `test_retriever_without_expander_keeps_child_only`；回滚方式为本卡提交回退，无独立配置开关（§8）；`app/rag/service.py` `60633D36…5B69BB3` |
| 与 RAG-029 统一组装入口 / RAG-037 状态契约兼容 | 通过 | E2 | 138 passed 回归含 `test_rag_029_pipeline_alignment`（16）、`test_rag_037_retrieval_status`（33）；status 仍按阈值 classify，unavailable 不读残留 |
| ruff / mypy | 通过 | E1 | 见 §4 命令与退出码 |
| 全量 pytest 全绿 | **未验证** | — | 全量 `2 failed, 1198 passed`；两条失败 SUT 不在本卡 diff（§7 取证）。按门禁如实记「未验证 / 有既有失败」，不写全绿。 |
| 真实检索质量 / 真实 LLM 生成评测 | 未验证 | E4 | 本卡 non-goal，未做真实调用；Mock 只证明链路。 |
| 独立子 Agent 入口级反例审查 | 未验证 | — | 由主 Agent / 独立复核方在关闭时执行；本次仅本方自测（串行自审非独立审查）。 |

**未验证项清单**：① 流式入口未单独跑真实端到端（同源推导，E4）；② 全量 pytest 非全绿
（两条无关既有失败：RAG-033 报告日期漂移、Milvus 删除 mock 契约）；③ 真实 LLM / Embedding
生成与检索质量评测（non-goal）；④ 独立第三方入口级反例审查（关闭门禁由主 Agent 触发）。
