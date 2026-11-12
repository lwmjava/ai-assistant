# MultiHop-RAG 补充检索对照

本报告是 **补充对照**，不替代官方 35 条质量门槛，也不作为查询改写或生效日过滤的采纳证据。
官方 35 条评估配置、holdout 决策规则和生产默认开关均未改动。

- 模式：`official`；质量证据（官方 35 条门槛）：`False`；补充对照：`True`；有效检索质量结果：`True`
- 运行时间：2026-11-11 17:35:16 Asia/Shanghai
- Embedding：`OpenAICompatibleEmbeddingProvider` / `text-embedding-v3` / 1024 维
- 代码：`git b2756f997fe4a1b159bc43279ecf1439e35745fd`；命令：`python evals/multihop_rag/run.py --mode official --reuse-index --retrieval-deadline-seconds 60.0 --report-stem rag-v0.1-multihop-rag-20261111-deadline60`
- 语料：609 篇；问题：2556 条；可计分（非 null_query）：2255 条
- 检索：本地向量库 / native / structured chunk 500/64，RRF k=60，深度 10 块
- 本跑检索时限：`60.0` 秒（仅对照进程内覆盖；生产默认 `8.0` 秒未改）
- 索引：复用已有评测库，未重嵌语料（indexed_documents=609，indexed_chunks=18395）
- 生产默认：`RAG_QUERY_REWRITE_ENABLED` 与 `RAG_EFFECTIVE_DATE_FILTER` 保持关闭；本评测未打开这两项

## 数据集与许可

- 数据集：MultiHop-RAG（Yixuan Tang, Yi Yang；COLM 2024）
- 论文：https://arxiv.org/abs/2401.15391
- GitHub：https://github.com/yixuantt/MultiHop-RAG
- Hugging Face：https://huggingface.co/datasets/yixuantt/MultiHopRAG
- 许可证：ODC-BY（https://opendatacommons.org/licenses/by/1-0/）

## 检索指标

指标与官方 35 条夹具相同：在去重后的文档序上计算 Recall@1/5/10、MRR、nDCG@10；
检索深度为 10 个块。null_query 没有期望文档，计入 not_scorable，不稀释召回。
引用覆盖率按命中块是否包含证据事实句计算。本对照不是对原论文 Hits@k（事实句包含）设置的复现。

- 案例数：2556（可计分 2255，不可计分 301）
- Recall@1：0.2941
- Recall@5：0.6467
- Recall@10：0.6928
- MRR：0.7946
- nDCG@10：0.6593
- 证据引用覆盖：2537/6084（0.4170）
- 越权案例：0（本数据集无 forbidden 文档）
- 延迟 p50/p95/max：11394.7 / 14009.3 / 23287.1 ms

## 按 question_type

| type | cases | scorable | R@1 | R@5 | R@10 | MRR | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| comparison_query | 856 | 856 | 0.3520 | 0.7229 | 0.7506 | 0.8206 | 0.7134 |
| inference_query | 816 | 816 | 0.2255 | 0.5525 | 0.6266 | 0.7724 | 0.5982 |
| null_query | 301 | 0 | n/a | n/a | n/a | n/a | n/a |
| temporal_query | 583 | 583 | 0.3050 | 0.6667 | 0.7004 | 0.7875 | 0.6655 |

## 调用量与费用

- 跑前估计：文本 2556 条，估计 token 190959，
预算闸门费用 0.0954795 元（public DashScope text-embedding-v3 Beijing list price 0.5 yuan/MTok (budget gate only, not a bill)）
- 实际嵌入调用 2557 次，文本 2557 条，
估计 token 191018，估计费用 未知


## 说明

- 未改官方 35 条门槛、holdout 决策规则、`evals/datasets/rag-v0.1/` 或生产默认。
- 冒烟模式只证明链路，不能作为质量证据。
- 切分与检索深度沿用冻结官方设置，与原论文默认 256 token 分块、Hits@k 不可直接横向对比。

- 时限选择：本跑仅在对照进程内将 RAG_RETRIEVAL_DEADLINE_SECONDS 覆盖为 60 秒，不改生产默认 8.0 秒。首次官方对照延迟 p50 约 8032ms，8 秒时限已被用尽；选择 60 秒是为查询嵌入 + 18395 块本地检索留足余量，而不是略高于 8 秒。
