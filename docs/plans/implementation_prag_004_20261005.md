# PRAG-004 实现说明（检索低分阈值与拒答，2026-10-05）

> 任务卡：PRAG-004（P0，L1，risk: retrieval-threshold-added）
> 状态：实现完成，验收条件满足；待人工审查后关闭
> 分支：nightly/rag-20261005

## 1. 完成了什么

相似度低于阈值的检索命中会少返回或全不返回；全部被过滤且原本有命中时返回明确拒答提示，不硬凑回答；低分命中写入可复现（JSON）日志。

- **关键事实修正**：卡里的"相似度 0.4"阈值不能直接套在 `ChunkResult.score` 上——`score` 是 **RRF 融合分**（恒 < 0.04），不是相似度。已按方案 A 让阈值作用于新增的 `similarity`（稠密余弦）字段。
- 新增配置 `RAG_MIN_SIMILARITY`（默认 0.4）与 `RAG_REFUSE_MESSAGE`（拒答提示）。
- `HybridRetriever.retrieve`：`similarity < 阈值` 的命中被过滤；全部被滤且原本有命中 → 返回拒答提示、`last_hits=[]`；否则拼接上下文。
- 低分过滤写日志（走 NFR-002 JSON 日志链路，含查询与阈值）。

## 2. 实际修改文件

| 文件 | 性质 |
|---|---|
| `app/rag/vectorstore/base.py` | `ChunkResult` 加 `similarity` 字段（默认 0.0，供阈值过滤；score 语义不变） |
| `app/rag/vectorstore/local.py` | `hybrid_search` 填充真实稠密相似度 `dense[idx]` |
| `app/rag/retriever.py` | 阈值过滤 + 全滤拒答 + 低分日志 |
| `app/core/config.py` | `RAG_MIN_SIMILARITY` / `RAG_REFUSE_MESSAGE` |
| `.env.example` | 同前配置 |
| `app/rag/backend/langchain_backend.py` | `similarity=float(score)`（score 即相似度） |
| `app/rag/service.py` | 父块展开继承子块 `similarity` |
| `app/rag/vectorstore/milvus.py` | 填 `similarity=1.0`（milvus 为 Partial，RRF 命中即有效；真实余弦待 RAG-015 补齐，避免默认 0 误滤） |
| `tests/test_rag_threshold.py` | 新增 4 个测试 |

## 3. 验证命令与结果

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest tests/test_rag_threshold.py -v` | 0 | **4 passed**（全高分保留 / 低分过滤 / 全滤拒答 / 无结果空串） |
| `pytest -k 'retriev' -q` | 1 | **14 passed**，2 skipped；4 errors 为 `data/pytest-tmp` 清理 PermissionError（环境性，非业务断言，同 NFR-002） |
| `ruff check <8 改动文件>` | 0 | All checks passed |
| `mypy <7 改动文件>` | 1 | **改动文件 0 新增错误**；3 errors 在 langchain/llamaindex 后端存量签名问题（REL-003 基线） |

## 4. 验收对照（卡 acceptance）

- [x] 相似度低于 0.4 时返回更少或不返回命中（`similarity < 0.4` 过滤，测试覆盖）。
- [x] 若拒答，有明确提示语（`RAG_REFUSE_MESSAGE`，测试断言等于该文案）。
- [x] 相似度低于 0.4 时有可复现的日志记录（`rag_low_similarity_filtered`，走 NFR-002 JSON 日志）。
- [x] 阈值与拒答文案有评测案例覆盖（`tests/test_rag_threshold.py` 4 用例）。
- [x] 阈值不得用 holdout 集调参（固定 0.4，未做超参搜索）。

## 5. 明确没做的事（Non-goals 边界）

- **未做**拒答后的兜底生成。
- **未改** `top_k` 默认值。
- **未强制**每一次低于 0.4 都拒答（仅在"原本有命中、全被过滤"时拒答）。
- **未用** holdout 调参。
- **未改** milvus 数据/索引（仅填防御性 similarity=1.0，milvus 仍为 Partial）。
- **未 commit、未合并、未部署、未推送**（夜间红线）。

## 6. 残余风险与说明

- **milvus 后端相似度**：milvus 为 Partial，暂以 1.0 占位避免误滤；真实余弦相似度由 RAG-015（Milvus 切换落地）补齐。
- **langchain/llamaindex 后端**：存量 mypy 签名错误（add_texts/from_texts/add LSP）非本卡引入，属项目基线治理事项。
- **父块展开**：`RAGService._expand_parent_chunks` 路径不经 HybridRetriever 阈值过滤（那是另一检索入口）；父块 `similarity` 已继承子块，保持一致。
- 阈值固定 0.4（2026-10-02 拍板）；如需调整属配置变更，不属本卡调参。

## 7. 次日人工审查清单

1. 复核 `pytest tests/test_rag_threshold.py`（4 passed）与 `pytest -k 'retriev'` 的 14 passed。
2. 确认 4 errors 确为 pytest-tmp 清理环境问题（删除 data/pytest-tmp 后复跑可验证）。
3. 实机验证：构造低相似度查询，确认命中变少/拒答提示 + JSON 日志含 rag_low_similarity_filtered。
4. 确认方案 A（新增 similarity 字段）的契约扩展可接受；决定 PRAG-004 关闭。
5. 提醒：milvus 后端相似度占位待 RAG-015 补真实余弦。
