# 词面覆盖重排对照

> 变量：`lexical_coverage`
> 运行时间：2026-10-02T09:07:05.597627+00:00
> 数据集：`rag-eval@0.1.0`
> 语料版本：`0.1.0`
> 代码：`2d3e23c8fa7127ccc2d7dd10d24a40a64fe86181`
> 命令：`python scripts/run_rag_baseline.py --mode official --rerank lexical`
> 工作目录：`E:\culture\SmartCustomerServiceSystem\ai-assistant`
> 嵌入：OpenAICompatibleEmbeddingProvider / text-embedding-v3 / 1024 维
> 向量库：local
> 机器：Windows 11 AMD64，Python 3.12.0，CPU 12
> 决策只使用 development 与 validation。holdout 仅记录，不参与判断，也不改公式。
> 重排发生在 hybrid_search 已经截断之后，深度 10，融合常数 60。没有第二次检索。
> 本轮不能作为生产检索质量结论。信号与 BM25 同源。独立 Reranker 仍是 Planned。
> 本文件不改写 `evals/reports/rag-v0.1-baseline-20260919.json`。

## 1. 结论

不采纳 lexical_coverage：调参集上没有同时提升 Recall@1 与 MRR 且不增加越权案例

决策：`keep`。
索引文档 13 篇，分块 37 个。
对照顺序是已截断的倒数排名融合顺序。处理顺序只按词面覆盖率重排这同一批。

## 2. 调参 split（development + validation）

| 顺序 | Recall@1 | MRR | 越权案例 |
|---|---|---|---|
| 对照 | 0.7976 | 0.9286 | 0 |
| 词面覆盖 | 0.7976 | 0.9167 | 0 |

## 3. holdout（不参与决策）

| 顺序 | Recall@1 | MRR | 越权案例 |
|---|---|---|---|
| 对照 | 0.7857 | 0.9286 | 0 |
| 词面覆盖 | 0.7857 | 0.9286 | 0 |
