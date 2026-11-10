# 查询改写单变量对照

- 模式：`official`；质量证据：`True`
- 决策：`not_improved`（holdout 不参与）
- 原因：改写未在触发切片上把 Recall@5 提到 0.90 以上，或损害了主指标/安全切片
- 改写：确定性扩展，不调用聊天模型；生产路径默认关闭，可用 `RAG_QUERY_REWRITE_ENABLED=true` 开启
- Embedding：`OpenAICompatibleEmbeddingProvider` / `text-embedding-v3` / 1024 维
- 代码：`git 7d6b761bdbfc03e5d2d3abbfcf4085eb15bb4d67`；命令：`python evals/query_rewrite/run.py --mode official`
- 进入条件（已落盘）：任一切片 Recall@5 跌破 0.90 即实施。AGENTS.md §14 仍写 overall 口径，本卡未改该文件。

## 冻结配置下的单变量结果（RAG_EFFECTIVE_DATE_FILTER=false）

| 指标 | 对照（原查询） | 处理（改写） |
| --- | --- | --- |
| overall Recall@5 | 0.9714285714285714 | 0.9714285714285714 |
| 调参集 Recall@5 | 0.9642857142857143 | 0.9642857142857143 |
| validation Recall@5 | 0.8333333333333334 | 0.8333333333333334 |
| effective_date Recall@5 | 0.6666666666666666 | 0.6666666666666666 |
| holdout Recall@5（仅记录） | 1.0 | 1.0 |
| 越权案例 | 0 | 0 |

官方冻结配置只检索 `is_current` 文档。`doc-billing-future` 属于 scheduled 预告，在此配置下不会进入候选集；改写单独无法召回 rag-031，这是已知限制，不是把过滤开关偷偷打开。

## 诊断：临时打开生效日过滤（不作为单变量改写结论，不改生产默认）

| 指标 | 过滤开 + 原查询 | 过滤开 + 改写 |
| --- | --- | --- |
| overall Recall@5 | 1.0 | 1.0 |
| 调参集 Recall@5 | 1.0 | 1.0 |
| validation Recall@5 | 1.0 | 1.0 |
| effective_date Recall@5 | 1.0 | 1.0 |
| holdout Recall@5（仅记录） | 1.0 | 1.0 |
| 越权案例 | 0 | 0 |

## 调用量

- 嵌入调用 61 次，文本 85 条，估计 token 1200，估计费用 未知

## 结论与建议

冻结配置下，改写没有提升已跌破的 validation / effective_date 切片，overall 与调参集也持平，越权案例仍为 0。主因仍是 rag-031：官方冻结配置关闭生效日过滤，只检索 `is_current` 文档，`doc-billing-future` 进不了候选集。

进程内临时打开生效日过滤的诊断（不作为本卡单变量结论，也未改生产默认）中，validation 与 effective_date 的 Recall@5 均到 1.0，且与是否改写无关。因此本卡把生产改写默认保持关闭；要修复这些切片，应在独立变更里评估 ADR-0003 的生效日过滤，而不是把过滤开关混进本次对照。
