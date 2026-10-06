# RAG-016 父块展开锁定实施计划

> 夜间任务（2026-10-06）。任务卡：RAG-016「父块展开：命中子块追加父块（方案 A）」。
> 状态：backlog → 本夜补回归锁定，不改行为契约；不动知识库/索引/数据库数据。

## 目标

锁定知识库搜索（`RAGService.search`）的父块展开行为，补齐边界回归用例，使卡验收标准有可复现证据：
- 命中带 parent_id 的子块时，返回集含对应父块；
- 不以「结果仅含父块、不含子块」作为合格线；
- 回归用例覆盖展开与去重。

## 现状（已核实代码事实）

- `app/rag/service.py::search`（L483-507）：检索后调用 `_expand_parent_chunks(hits)`，知识库搜索路径**已有展开**。
- `_expand_parent_chunks`（L509-535）：命中子块（`parent_id` 非空）时追加父块并去重；父块继承子块 `score`/`similarity`/`version_status`；子块记录不在库、父块记录不存在时安全跳过；只展开一层（不递归）。
- `tests/test_rag.py` 已有 3 个展开测试（L350/L392/L440）：子块命中含父块、子块保留且继承评分、父子同时命中去重。实测 **3 passed**。
- 对话路径 `make_retriever`/`HybridRetriever` 不经展开——**按卡 non_goals，对话父块选入与预算由 RAG-030 承接，本卡不改**。

## 方案

新增独立边界回归测试 `tests/test_rag_parent_expand_lock.py`（避免与 `tests/test_rag.py` 未提交改动混淆），复用既有测试模式（`session` fixture + `RAGService.ingest_text(strategy="parent_child")` + monkeypatch `_store.hybrid_search` + `rag.search`）：

1. `test_parent_hit_not_expand_downward`：命中父块本身 → 返回集仅父块，不追加其子块（锁定"只向上展开，不向下展开"）。
2. `test_child_with_missing_parent_skipped`：子块有 `parent_id` 但父块记录不存在 → 不崩溃，返回集仅子块。
3. `test_expand_inherits_version_status`：父块继承子块 `version_status`（现有测试未断言该字段）。
4. `test_expand_only_one_level`：父块自身也有 `parent_id`（人为构造三层链）→ 只追加直接父块，不递归到祖父块（锁定方案 A 一层语义）。

预期：4 个边界测试全部通过（实现已满足），作为展开行为锁定的回归基线。若任何用例暴露实现缺口，则按卡 allowed_paths 修复 `app/rag/service.py` 后重跑。

## 非目标

- 不改对话路径（`make_retriever`/`HybridRetriever`）——RAG-030 承接。
- 不做「只返回父块」模式。
- 不改切分策略、Embedding、RRF、阈值。
- 不操作知识库/索引/数据库数据。
- 不新增未批准依赖、不改公共契约。

## 范围

allowed_paths（与卡一致）：`app/rag/`（仅当测试暴露缺口）、`tests/`、`docs/`、`tasks.yaml`。
本计划预计只新增 `tests/test_rag_parent_expand_lock.py` 与 `docs/plans/` 下计划/实现说明；不修改业务代码（除非测试失败暴露缺口）。

## 验收

- 新增 4 个边界测试全部通过（`pytest tests/test_rag_parent_expand_lock.py -q`，退出 0）。
- 展开相关既有测试仍通过（`pytest tests/test_rag.py -k "expand or parent_expand" -q`，退出 0）。
- RAG 相关回归：`pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -v`（记录退出码；既有失败项记录不掩盖）。
- `ruff check --no-cache tests/test_rag_parent_expand_lock.py`、`mypy tests/test_rag_parent_expand_lock.py --cache-dir data/mypy-rag016`（记录退出码）。
- 实现说明落盘 `docs/plans/implementation_rag_016_parent_expand_lock_20261006.md`；tasks.yaml 的 RAG-016 加 progress 标注（不改 status_values，不标 done，留待人工验收）。

## 风险与停止条件

- 风险：L1，side_effect `parent-expand-lock`；不触数据，回滚 = 删除新增测试文件与文档提交。
- 停止条件：任何测试暴露需改动 `allowed_paths` 之外文件、需新增依赖/改公共契约、基线失败无法隔离、同一验证修复 3 次不过 → 停止扩大修改并记录。
