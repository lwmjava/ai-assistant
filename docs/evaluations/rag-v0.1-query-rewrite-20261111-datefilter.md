# 查询改写单变量对照

- 模式：`official`；质量证据：`True`
- 决策：`not_improved`（holdout 不参与）
- 原因：改写未在触发切片上把 Recall@5 提到 0.90 以上，或损害了主指标/安全切片
- 改写：确定性扩展，不调用聊天模型；生产路径默认关闭，可用 `RAG_QUERY_REWRITE_ENABLED=true` 开启
- Embedding：`OpenAICompatibleEmbeddingProvider` / `text-embedding-v3` / 1024 维
- 代码：`git bbdf246e8306d5d897623d4fa4872df620d1846d`；命令：`python evals/query_rewrite/run.py --mode official --report-stem rag-v0.1-query-rewrite-20261111-datefilter --decision-effective-date-filter`
- 进入条件（已落盘）：任一切片 Recall@5 跌破 0.90 即实施。AGENTS.md §14 仍写 overall 口径，本卡未改该文件。
- 官方决策配置：进程内 RAG_EFFECTIVE_DATE_FILTER=true；未改 .env / config.py 生产默认

## 官方决策对照（进程内 RAG_EFFECTIVE_DATE_FILTER=true，改写开 vs 关；未改生产默认）

| 指标 | 对照（原查询） | 处理（改写） |
| --- | --- | --- |
| overall Recall@5 | 1.0 | 1.0 |
| 调参集 Recall@5 | 1.0 | 1.0 |
| validation Recall@5 | 1.0 | 1.0 |
| effective_date Recall@5 | 1.0 | 1.0 |
| holdout Recall@5（仅记录） | 1.0 | 1.0 |
| 越权案例 | 0 | 0 |

本跑经授权把生效日过滤纳入官方决策配置，仅在评测进程内覆盖冻结值，不修改生产开关，也不放宽门槛。

## 调用量

- 嵌入调用 61 次，文本 85 条，估计 token 1200，估计费用 未知

