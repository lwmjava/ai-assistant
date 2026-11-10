# MultiHop-RAG 补充检索对照

> **本报告不可作为检索质量证据。**
>
> 这是首次官方对照的原始结果，予以保留，不被后续重跑覆盖。2556 条问题 **全部失败**：2528 条 `MODEL_OR_RUNTIME_FAILURE:DeadlineExceededError`，28 条 `TIMEOUT`；全部 `retrieved_document_ids` 为空，因此 Recall/MRR/nDCG 与证据引用覆盖率均为 0。
>
> 失败原因是评测进程使用了生产默认的 `RAG_RETRIEVAL_DEADLINE_SECONDS=8.0`（覆盖查询嵌入 + 本地向量检索），延迟 p50 约 8032.3ms，说明 8 秒时限已被用尽。这不是 gold-ID / 计分错误，也不能代表检索质量。语料索引本身是完整的（609 篇、18395 块，`data/eval_multihop_rag.db`），应复用而非重嵌。查询阶段还出现过间歇性嵌入 `ConnectTimeout` / `ConnectError` / `rag_deadline_exhausted`。

本报告是 **补充对照**，不替代官方 35 条质量门槛，也不作为查询改写或生效日过滤的采纳证据。
官方 35 条评估配置、holdout 决策规则和生产默认开关均未改动。

- 模式：`official`；质量证据（官方 35 条门槛）：`False`；补充对照：`True`；有效检索质量结果：`False`
- 运行时间：2026-11-11 07:08:59 Asia/Shanghai
- Embedding：`OpenAICompatibleEmbeddingProvider` / `text-embedding-v3` / 1024 维
- 代码：`git 530b350aed422c8c2c0d520ac17f96188f885a06`；命令：`python evals/multihop_rag/run.py --mode official`
- 语料：609 篇；问题：2556 条；可计分（非 null_query）：2255 条
- 检索：本地向量库 / native / structured chunk 500/64，RRF k=60，深度 10 块
- 本跑检索时限：`8.0` 秒（生产默认，未做对照进程内覆盖）
- 生产默认：`RAG_QUERY_REWRITE_ENABLED` 与 `RAG_EFFECTIVE_DATE_FILTER` 保持关闭；本评测未打开这两项；生产默认 `RAG_RETRIEVAL_DEADLINE_SECONDS=8.0` 未被本报告改动

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

**下列全 0 指标是空召回的产物，不能当作质量结论。**

- 案例数：2556（可计分 2255，不可计分 301）
- Recall@1：0.0000
- Recall@5：0.0000
- Recall@10：0.0000
- MRR：0.0000
- nDCG@10：0.0000
- 证据引用覆盖：0/6084（0.0000）
- 越权案例：0（本数据集无 forbidden 文档）
- 延迟 p50/p95/max：8032.3 / 20449.0 / 91405.4 ms

## 按 question_type

| type | cases | scorable | R@1 | R@5 | R@10 | MRR | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| comparison_query | 856 | 856 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| inference_query | 816 | 816 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| null_query | 301 | 0 | n/a | n/a | n/a | n/a | n/a |
| temporal_query | 583 | 583 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

## 调用量与费用

- 跑前估计：文本 20951 条，估计 token 2181873，
预算闸门费用 1.0909365 元（public DashScope text-embedding-v3 Beijing list price 0.5 yuan/MTok (budget gate only, not a bill)）
- 实际嵌入调用 3165 次，文本 20951 条，
估计 token 2181873，估计费用 未知

## 说明

- 未改官方 35 条门槛、holdout 决策规则、`evals/datasets/rag-v0.1/` 或生产默认。
- 冒烟模式只证明链路，不能作为质量证据。
- 切分与检索深度沿用冻结官方设置，与原论文默认 256 token 分块、Hits@k 不可直接横向对比。
- 本文件与 `evals/reports/rag-v0.1-multihop-rag-20261110.json` 是首次官方对照的失败记录，后续带更高对照进程内时限的重跑应写入新文件，不得覆盖本对报告。
