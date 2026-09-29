# 实现说明：补记知识库治理三项的验证输出

> 日期：2026-09-29
> 任务：`EVD-001`
> 结果：规定命令全部退出码为 0。清点表第 1–3 行改为完成。`tasks.yaml` 中本任务标为 `done`。下一项是 `EVD-002`。

## 完成的行为

按 `docs/plans/plan_delivery_16_evidence.md` 原样重跑知识库治理三项的验证命令，并把输出写进三份计划第 8 节。

当天下午早些时候，RAG-012 / RAG-013 的 pytest 退出码为 1（3 failed, 66 passed），ruff 退出码为 1（`app/rag/embeddings/mock.py` UP012）。第 1–3 行当时保持未完成。混合检索重跑隔离与 Mock 编码检查见 `docs/plans/implementation_rag_hybrid_rerun_isolation.md`。修好之后再次原样执行。

解释器：conda 环境 `ai-assistant`（`D:\install\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0）。

RAG-011：

| 命令 | 退出码 | 输出摘要 |
|---|---|---|
| `pytest tests/test_rag_access.py tests/test_rag_permission_semantics.py tests/test_rag_import_jobs.py -v` | 0 | 30 passed, 1 warning in 37.08s |
| `ruff check app/rag/access.py app/rag/import_jobs.py app/rag/service.py app/api/routes/rag.py` | 0 | All checks passed! |
| `mypy app/rag/access.py app/rag/import_jobs.py app/rag/service.py` | 0 | Success: no issues found in 3 source files |
| `cd frontend; npm run typecheck` | 0 | `tsc --noEmit`，无输出 |

RAG-012 与 RAG-013 命令文本相同，只跑一次：

| 命令 | 退出码 | 输出摘要 |
|---|---|---|
| `pytest tests/test_rag_access.py tests/test_rag_permission_semantics.py tests/test_rag.py tests/test_rag_import_jobs.py -v` | 0 | 69 passed, 1 warning in 64.93s |
| `ruff check app/rag/ app/models/rag.py app/api/routes/rag.py` | 0 | All checks passed! |
| `mypy app/rag/access.py app/rag/service.py app/models/rag.py` | 0 | Success: no issues found in 3 source files |
| `cd frontend; npm run typecheck` | 0 | 引用 RAG-011 同一次运行，没有重跑 |

原先失败的三条测试这次都是 PASSED：`test_hybrid_search_bm25_all_zero_preserves_dense_order`、`test_hybrid_search_bm25_positive_keeps_rrf_and_skips_diag`、`test_hybrid_search_bm25_all_zero_reason_empty_doc_tokens`。

`tasks.yaml` 里 `EVD-001` 改为 `done`。`AGENTS.md` 的下一项改为 `EVD-002`，条数改为 108 条（52 done / 18 ready / 38 backlog）。清点表完成 13 项，未完成 3 项（4、13、15）。80% 未达到。

## 代码位置

- RAG-011：`docs/plans/plan_rag_011_control_plane.md` 第 8 节。上午的失败输出和类型修复后的通过记录都保留，收口结果在「验证通过」。
- RAG-012、RAG-013：`docs/plans/plan_rag_012_soft_delete.md`、`docs/plans/plan_rag_013_version_states.md` 第 8 节。失败记录保留，收口结果在「验证通过」。两份计划贴同一份输出。
- 清点表：`docs/plans/record_delivery_16.md` 第 1–3 行结论改为完成。
- 本任务收口时没有修改 `app/` 或 `frontend/`。

## 没做的事

没有补浏览器走查、真实模型、Milvus 或生产库迁移。没有修改 `docs/AI辅助开发迭代指导.md`，该文件不在本任务允许路径内；其中「下一项是 EVD-001」与本次收口后的状态不一致，留待允许改该文件时再改。
