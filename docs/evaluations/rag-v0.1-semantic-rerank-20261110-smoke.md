# 稠密语义相似度重排对照

> 变量：`semantic_similarity`
> 运行时间：2026-11-10T02:43:08.543377+00:00
> 数据集：`rag-eval@0.1.0`
> 语料版本：`0.1.0`
> 代码：`git 507d21f6e57b083dc34316a33ff52b81f448a640`
> 命令：`python evals/semantic_rerank/run.py --mode smoke`
> 工作目录：`E:\culture\SmartCustomerServiceSystem\ai-assistant`
> 嵌入：MockEmbeddingProvider / mock-embedding / 64 维
> 向量库：local
> 机器：Windows 11 AMD64，Python 3.12.0，CPU 12
> 本轮是冒烟链路验证，使用 Mock 嵌入，不能作为质量或采纳证据。
> 重排发生在 hybrid_search 已经截断之后，深度 10，融合常数 60。没有第二次检索，也没有改 Embedding 或切分。
> 未把该候选接到生产检索路径，默认行为保持融合顺序。
> 本文件不改写 `evals/reports/rag-v0.1-baseline-20260919.json`。

## 1. 结论

冒烟模式只证明链路，不能作为质量或采纳证据

决策：`not_evaluated`。原因码：`mock_not_quality_evidence`。
索引文档 13 篇，分块 37 个。
对照顺序是已截断的倒数排名融合顺序。处理顺序只按稠密余弦相似度重排这同一批。

## 2. 调参 split（development + validation）

| 顺序 | Recall@1 | Recall@5 | MRR | 越权案例 | p95(ms) |
|---|---|---|---|---|---|
| 对照 | 0.7798 | 0.9643 | 0.9286 | 0 | 12.9 |
| 语义相似度 | 0.7262 | 0.9464 | 0.8869 | 0 | 12.9 |

## 3. holdout（不参与决策）

| 顺序 | Recall@1 | Recall@5 | MRR | 越权案例 | p95(ms) |
|---|---|---|---|---|---|
| 对照 | 0.6429 | 1.0000 | 0.8571 | 0 | 4.0 |
| 语义相似度 | 0.5000 | 0.9286 | 0.7619 | 0 | 4.0 |

## 4. 调用量、费用、内存与失败切片

嵌入调用 57 次，文本 81 条，字符 4660。token 估计 1165，方法为 utf8_bytes_divided_by_4。
未配置嵌入单价，费用未知，不编造账单。
重排内存增量 0 字节；运行后常驻 None 字节；峰值 None 字节。

处理组失败原因：
- `EVIDENCE_QUOTE_MISSED`：3 条，编号 rag-031, rag-040, rag-041
- `EXPECTED_DOC_MISSED`：1 条，编号 rag-031
