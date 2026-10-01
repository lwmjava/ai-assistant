# 实现说明：混合检索重跑隔离与 Mock 编码检查

> 日期：2026-09-29
> 对应计划：`docs/plans/plan_rag_hybrid_rerun_isolation.md`

## 做了什么

全量 RAG 测试只有 4 处失败，已一次改完。

- `tests/test_rag.py` 里四个混合检索用例每次使用新的租户号。`_seed_hybrid_chunks` 会提交到共用测试库，写死的租户号会把上次的分块再算进排序。
- `app/rag/embeddings/mock.py` 的 `encode("utf-8")` 改为 `encode()`。默认编码仍是 UTF-8。

检索排序逻辑没有改。

## 验证

解释器：conda 环境 `ai-assistant`。

全量（21 个测试文件）退出码 0：`201 passed, 1 skipped, 1 warning in 179.99s`。

同一四条混合检索用例紧接着再跑一次，退出码 0：`4 passed`。用来确认共用库里已有旧数据时不再失败。

`ruff check app/rag app/models/rag.py app/api/routes/rag.py` 退出码 0：`All checks passed!`。

## 没做的事

没有重跑知识库治理三项的核对命令，也没有改清点表或任务状态。
