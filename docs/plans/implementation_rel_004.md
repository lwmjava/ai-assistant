# 实现说明：一轮词面覆盖重排实验

> 日期：2026-10-02
> 计划：`docs/plans/plan_d4.md` 的 REL-004
> 结果：不采纳。调参集上 Recall@1 持平，MRR 下降，越权案例数仍为 0。本地检索顺序没有改。这不是生产检索质量结论。

## 完成的行为

正式命令在仓库根目录执行：

`python scripts/run_rag_baseline.py --mode official --rerank lexical`

退出码 0。省略 `--rerank` 时，脚本仍按原来的融合顺序评测，不会重排。

这次只建一次索引，每条案例只做一次真实嵌入和一次 `hybrid_search`。对照使用已经截断的融合顺序，深度 10，融合常数 60。处理只对这同一批结果按词面覆盖率重排：查询词集合与分块词集合的交集大小，除以查询词集合大小；覆盖率相同则保持原次序。分词使用 `app.rag.embeddings.mock.tokenize`。没有扩大候选池，没有第二次检索，没有改 k、切分、嵌入、BM25 或 Milvus。

打分使用现行评测器的 Recall、MRR 和越权命中定义。决策只看 development 与 validation。holdout 写在报告里，没有用来判断，也没有改公式。

## 指标

- 工作目录：`E:\culture\SmartCustomerServiceSystem\ai-assistant`
- 代码提交：`2d3e23c8fa7127ccc2d7dd10d24a40a64fe86181`
- 数据集：`rag-eval@0.1.0`，语料版本 `0.1.0`
- 嵌入：`OpenAICompatibleEmbeddingProvider`，模型 `text-embedding-v3`，维度 1024
- 向量库：`local`
- 机器：Windows 11 AMD64，Python 3.12.0，12 个 CPU
- 索引：13 篇文档，37 个分块

调参集（development + validation）：

| 顺序 | Recall@1 | MRR | 越权案例 |
|---|---|---|---|
| 对照 | 0.7976 | 0.9286 | 0 |
| 词面覆盖 | 0.7976 | 0.9167 | 0 |

原因码是 `primary_regression`：Recall@1 没有提高，MRR 从 0.9286 降到 0.9167。三条规则要求 Recall@1 和 MRR 都高于对照，且越权案例数不高于对照。这次不成立，决策为 `keep`。

holdout 仅记录：两边 Recall@1 都是 0.7857，MRR 都是 0.9286，越权案例都是 0。

## 代码位置

- `scripts/run_rag_baseline.py` 的 `--rerank lexical`
- `tests/eval/lexical_rerank.py`
- `tests/eval/test_lexical_rerank.py`
- `tests/eval/harness.py` 的 `score_hits`：同一套打分。原有 `run_case` 仍走 `RAGService.search`
- `docs/evaluations/rag-v0.1-lexical-coverage-20261002.md`
- `evals/reports/rag-v0.1-lexical-coverage-20261002.json`

## 验证

`pytest tests/eval/test_lexical_rerank.py -q`：4 passed，退出码 0。

正式命令退出码 0。冻结基线文件没有被改写。报告里没有密钥和连接串。

## 没做的事

没有改 `app/rag/` 的检索顺序，没有改 `app/core/config.py`，没有改 `.env.example`。没有把重排默认打开，也没有留下默认关闭的生产开关。独立 Reranker 仍是 Planned。没有做查询变换，没有扫 k，没有用 Mock 或 smoke 当作采纳证据。开关、灰度和开销没有做；对应的落地卡已关闭。
