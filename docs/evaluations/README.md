# Evaluation 报告目录

> 状态：RAG v0.1 数据集、Gold v0.1（24 条）与 **首份检索基线（2026-09-19，真实嵌入）** 均已落地
> 更新日期：2026-09-20

| 文件 | 说明 |
|---|---|
| `rag-v0.1-evaluation-plan.md` | 数据集计划（无发布阈值） |
| `rag-v0.1-independent-review.md` | 程序 + 二次通读校验 |
| `rag-v0.1-gold-review-batch.md` | 人工 Gold v0.1（24 条，阿明，2026-09-13） |
| `rag-v0.1-baseline-report.md` | `RAG-005` 检索基线报告（指标、失败案例、有效性限制） |

实际数据集在 `evals/datasets/rag-v0.1/`，机器可读基线在 `evals/reports/`。

## 当前基线口径（`RAG-005`，2026-09-19）

Recall@1 = 0.795、MRR = 0.929、nDCG@10 = 0.942（35 条可排序案例）；
证据原文覆盖 45/49 = 0.918；越权命中 3/16 = 0.1875；检索 P50 531 ms / P95 613 ms。

引用这些分数时必须同时说明有效性限制：语料仅 13 篇 / 37 个分块，
Recall@5 与 @10 接近 1.0 主要来自候选池过小，**不代表生产检索质量**。
详见报告第 6 节。

2026-09-19 之后：A1 修订了越权口径（见 Gold 批次补记与 ADR-0001 §9）；
B1 已落地检索注入块剔除与不可信围栏；C1 的生效日期语义见 **Accepted** `docs/adr/0003-effective-date-versioning.md`（`RAG_EFFECTIVE_DATE_FILTER` 默认关闭）。
2026-09-19 JSON 基线分数不改写。

本轮未测真实 LLM 生成层，因此答案级 Citation、拒答行为和 Gold `forbidden_answer_points` 仍未用真实模型验证。
`RAG-006` 已用脚本化 LLM 证明：摄取注入文档后 `refund_tool` 不会执行，RAG 上下文带不可信围栏。
在 Citation / 真实 LLM Evaluation 补齐前，不得在 README、PRD 或对外材料中宣称 RAG 已达到任何发布阈值。
