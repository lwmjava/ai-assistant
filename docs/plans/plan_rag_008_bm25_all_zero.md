# RAG-008：BM25 全 0 时稀疏路不进 RRF

> 状态：已完成（`tasks.yaml` RAG-008 `done`，2026-09-24）
> 分支：`fix/rag-005-006-review`
> 来源：评审修复方案阶段 1 / M1；Cursor 实施计划归档
> 日期：2026-09-24

BM25 全 0 时 Local 混合检索不再把载入顺序送进 RRF，融合结果等于稠密排序，并打一条不含正文的 info 诊断。有正分时排序保持不变。

## 目标

[`app/rag/vectorstore/local.py`](../../app/rag/vectorstore/local.py) 的 `hybrid_search` 在 BM25 全为 0 时，不再执行：

```python
        if any(s > 0 for s in bm25):
            sparse_order = list(np.argsort(-np.array(bm25)).tolist())
        else:
            sparse_order = list(range(len(valid)))
```

改为 `sparse_order = []`。现有 `_rrf` 对空列表不加分，融合顺序等于稠密顺序。同时 `logger.info` 事件 `bm25_all_zero`。

## 非目标

不改分词器、BM25 k1/b、默认 `RAG_HYBRID_RRF_K`、Milvus、评测报告、反思语料。不把全 0 记成 error/warning。不宣称检索质量提升。不改 `AGENTS.md`（不在本任务 `allowed_paths`）。

## 行为

原因码按此顺序只记一个：

- `empty_query_tokens`：查询词项为空（即使候选词项也为空）
- `empty_doc_tokens`：查询有词项，且每个候选词项都为空
- `no_overlap`：两边都有词项，但没有重合

日志字段只含 `tenant_id`、`candidate_count`、`query_token_count`、`empty_doc_count`、`reason`。不写查询原文、分块正文、词项列表、文档标识。

有大于 0 的 BM25 分时，仍走现有 `argsort`，不打这条日志。

## 实施

先在 [`tests/test_rag.py`](../../tests/test_rag.py) 写失败用例，沿用 `test_hybrid_search_skips_mismatched_embedding_dimensions` 的建块方式（显式 `embedding` / `tokens` JSON）。分块主键是 UUID，SQL 无 `ORDER BY`，因此断言不依赖载入顺序：用严格递减的余弦向量，断言命中内容顺序等于余弦降序。

- 全 0：查询词与候选不重合，且插入顺序与余弦降序相反。修复前 RRF 会把载入顺序混进去，顺序不等于稠密顺序；修复后等于。
- 有正分：至少一块 BM25 大于 0，且稀疏排名与载入顺序不同。断言融合顺序仍等于两路 `_rrf`，且不出现 `bm25_all_zero`。
- 三条原因码：`caplog` 设为 INFO，断言事件名、原因码和计数字段；正文、词项、文档 id 中的哨兵字符串不得出现在日志里。

再改 `local.py`：抽出原因判定，全 0 分支改为空稀疏路并打 info。正分分支保持不动。

完成后只把 [`tasks.yaml`](../../tasks.yaml) 里 RAG-008 标为 `done` 并补事实与 `completed_at`。不开始 RAG-009。

### 实施清单

| 项 | 内容 | 状态 |
|---|---|---|
| failing-tests | 在 `tests/test_rag.py` 先写全 0 排序、正分排序不变、三条原因码与日志字段的失败用例 | done |
| local-hybrid | `local.py`：全 0 时 `sparse_order` 为空，打 `bm25_all_zero` info；正分路径不变 | done |
| verify-close | 跑 pytest/ruff/mypy，通过后把 `tasks.yaml` 的 RAG-008 标为 done | done |

## 验收

```powershell
pytest tests/test_rag.py -v
ruff check app/rag/vectorstore/local.py tests/test_rag.py
mypy app/rag/vectorstore/local.py
```

## 风险与回滚

只改变 BM25 全 0 时的 Local 排序。回滚是恢复用 `range(len(valid))` 作为稀疏排名。不重跑 rag-v0.1。`AGENTS.md` §14 的当前任务指针会暂时仍写 RAG-008，留到后续文档同步。
