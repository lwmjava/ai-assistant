# 生效日过滤单变量对照

本报告只评估 `RAG_EFFECTIVE_DATE_FILTER` 这一变量，与查询改写结论分开。评测过程不改写查询，不改切分 / Embedding / Reranker，也不把生产默认改为开启。

- 模式：`official`；质量证据：`True`
- 决策：`improved`（holdout 不参与）
- 是否按查询日召回未来生效文档（rag-031）：是
- 原因：打开生效日过滤后，按查询日召回了未来生效文档，触发切片 Recall@5 提升，且未破坏越权切片与 overall/调参集
- Embedding：`OpenAICompatibleEmbeddingProvider` / `text-embedding-v3` / 1024 维
- 代码：`git ac6994f3309b0d04fe646759425cec5eee8c23a6`；命令：`python evals/effective_date_filter/run.py --mode official`
- 生产默认：`RAG_EFFECTIVE_DATE_FILTER=false`，本实验仅在评测进程内覆盖打开处理组

## 单变量结果（查询改写关闭）

| 指标 | 对照（过滤关闭） | 处理（过滤打开） |
| --- | --- | --- |
| overall Recall@5 | 0.9714285714285714 | 1.0 |
| 调参集 Recall@5 | 0.9642857142857143 | 1.0 |
| validation Recall@5 | 0.8333333333333334 | 1.0 |
| effective_date Recall@5 | 0.6666666666666666 | 1.0 |
| holdout Recall@5（仅记录） | 1.0 | 1.0 |
| 越权案例 | 0 | 0 |

对照应对齐查询改写第一轮官方结果中过滤关闭、原查询的分数：overall 0.9714（34/35）、调参集 0.9643、validation 0.8333（n=6）、effective_date 0.6667、越权 0。

## 调用量

- 嵌入调用 57 次，文本 81 条，估计 token 1165，估计费用 未知

## 说明

- 本结果不是查询改写是否采纳的证据。
- 即使处理组提升，也不在本提交中打开生产默认。
