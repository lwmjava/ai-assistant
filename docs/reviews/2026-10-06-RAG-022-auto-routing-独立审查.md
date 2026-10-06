# RAG-022 auto-routing 独立审查

日期：2026-10-06。审查对象：RAG-022 自动切分策略选择（auto-routing 增强）。审查视角：未参与主要实现的代码与契约审查。

## 审查范围

- `app/rag/chunking/routing.py`（新）
- `app/rag/chunking/factory.py`
- `app/rag/service.py`（ingest_parsed_document、reindex_document_in_place、新增静态方法）
- `tests/test_chunk_routing.py`（新）

## 维度结论

| 维度 | 结论 |
|---|---|
| 架构边界 | routing 为纯决策模块，不触 DB、不触 LLM、不触检索；位于 `app/rag/chunking/`，符合分层。factory 委托 routing，service 调用 factory，未引入新顶层依赖。 |
| 向后兼容 | `resolve_strategy_name(text, strategy)` 旧签名保留；旧 chunking 测试 22 条全绿。配置默认 `RAG_CHUNK_STRATEGY` 未改。 |
| 权限/租户 | 未改 ACL、租户过滤、版本过滤；路由仅基于解析产物信号，不越权读外部数据。 |
| 失败路径 | 旧策略全 NULL → 走路由；策略不一致 → 不强制复用，走路由；reindex 旧行在异步操作前删除并 flush，避免对象过期；`long_threshold_chars` 配置缺失时有离线回退。 |
| 测试有效性 | 覆盖 bbox→layout、blocks→format、Markdown→structured、纯文本不选 format/layout、代码→token_aware、请求覆盖、决策可解释、重解析复用、重解析兼容、父子范围包含。测试不依赖真实 Embedding。 |
| 性能 | 信号计算复用 RAG-021 已有的 `structure_units`（原本被 guard 调用），无额外结构扫描成本；无新 LLM/DB 调用。 |
| 范围越界 | 改动文件均在 allowed_paths（app/rag/、tests/、docs/、tasks.yaml）；未改 alembic、.env、embedding、检索、默认策略。 |

## 发现并修复的问题

1. **重解析 DELETE 行数不匹配**：初版在异步嵌入/向量清理后才删除旧 chunks，SQLAlchemy 对象过期导致 `SAWarning: DELETE expected N rows; 0 matched`。修复为读取策略后立即删除旧行并 flush，再做异步嵌入。复跑 `test_reparse_preserves_excluded_structure` 通过，警告消失。
2. **mypy 类型**：`configured` 来自 `getattr(..., "structured")` 被推断为 `str | None`，传给要求 `str` 的参数报错。修复为 `str(...)` 强转。
3. **ruff 未用导入**：测试初版残留未用的 `Chunk` / `factory_mod` / `RoutingSignals` / `_FakeEmbedding` / `_FakeSplitStrategy` 样板，已清理并 `ruff --fix`。
4. **死代码**：routing 初版导出未被调用的 `reuse_decision`，已删除。

## 未发现问题

- 未发现租户泄漏、权限越界、注入面扩大（路由仅消费 ParsedDocument 字段，不执行外部内容）。
- 未发现 embedding/检索/RRF 改动。
- 未发现默认策略被静默切换为 auto。

## 残余风险

- 本地 `.env` 的 `RAG_CHUNK_STRATEGY=parent_child` 与若干测试期望（结构化）不一致，导致 2 条既有测试在基线即失败；本卡未改 `.env`（forbidden），已在实现说明中标注。
- auto 路由决策表的阈值（CJK 0.3、长度 2×chunk_size、bbox 判定）尚未经真实文档集调参；non-goal 要求无指标不把 auto 设默认，故当前仅在显式 auto 时启用。

## 结论

可以关闭 RAG-022 的代码与自动验收部分；人工业务验收（如有）由负责人另行确认。
