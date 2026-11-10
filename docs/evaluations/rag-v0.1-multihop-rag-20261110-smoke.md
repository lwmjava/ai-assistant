# MultiHop-RAG 补充检索对照

本报告是 **补充对照**，不替代官方 35 条质量门槛，也不作为查询改写或生效日过滤的采纳证据。
官方 35 条评估配置、holdout 决策规则和生产默认开关均未改动。

- 模式：`smoke`；质量证据（官方 35 条门槛）：`False`；补充对照：`True`
- 运行时间：2026-11-10 23:54:59 Asia/Shanghai
- Embedding：`MockEmbeddingProvider` / `mock-embedding` / 64 维
- 代码：`git 59205a71682b5bfc024fe03fc445e2fafaa447fd`；命令：`python evals/multihop_rag/run.py --mode smoke`
- 语料：3 篇；问题：3 条；可计分（非 null_query）：2 条
- 检索：本地向量库 / native / structured chunk 500/64，RRF k=60，深度 10 块
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

- 案例数：3（可计分 2，不可计分 1）
- Recall@1：0.5000
- Recall@5：1.0000
- Recall@10：1.0000
- MRR：1.0000
- nDCG@10：0.9599
- 证据引用覆盖：4/4（1.0000）
- 越权案例：0（本数据集无 forbidden 文档）
- 延迟 p50/p95/max：4.4 / 8.9 / 8.9 ms

## 按 question_type

| type | cases | scorable | R@1 | R@5 | R@10 | MRR | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| comparison_query | 1 | 1 | 0.5000 | 1.0000 | 1.0000 | 1.0000 | 0.9197 |
| inference_query | 1 | 1 | 0.5000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| null_query | 1 | 0 | n/a | n/a | n/a | n/a | n/a |

## 调用量与费用

- 跑前估计：文本 6 条，估计 token 345，
预算闸门费用 0.0001725 元（public DashScope text-embedding-v3 Beijing list price 0.5 yuan/MTok (budget gate only, not a bill)）
- 实际嵌入调用 6 次，文本 6 条，
估计 token 345，估计费用 未知


## 说明

- 未改官方 35 条门槛、holdout 决策规则、`evals/datasets/rag-v0.1/` 或生产默认。
- 冒烟模式只证明链路，不能作为质量证据。
- 切分与检索深度沿用冻结官方设置，与原论文默认 256 token 分块、Hits@k 不可直接横向对比。
