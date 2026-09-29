# 混合检索重跑污染与 Mock 编码检查

> 日期：2026-09-29
> 状态：与本次修复一起落地。

全量 RAG 测试里只有 3 条失败，外加 `ruff check app/rag` 的 1 条。没有第三批。

3 条失败是同一原因：`_seed_hybrid_chunks` 会 `commit` 到共用的 `data/test_ai_assistant.db`，租户号写死。第二次跑会把上次的分块再算进去。日志里 `candidate_count=9`，而用例只插入了 3 条，排序因此和期望不一致。

改法：这四个用例每次用新的租户号。检索实现不改。

`app/rag/embeddings/mock.py` 第 36 行的 `.encode("utf-8")` 改成 `.encode()`。Python 3 默认就是 UTF-8，行为不变。

验收：同一批 RAG 测试与 `ruff check app/rag app/models/rag.py app/api/routes/rag.py` 退出码都为 0。
