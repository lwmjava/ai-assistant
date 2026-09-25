# RAG Evaluation Plan v0.1

> 复制自模板后的实际计划。本文件记录数据集契约，不记录 RAG-005 运行分数。  
> 日期：2026-09-13  
> 任务：`RAG-004`

## 1. 评估对象

- 能力/版本：RAG Retrieval/Citation 数据集 `rag-eval@0.1.0`
- 变更摘要：首次建立 Case Schema、合成语料、Silver/Adversarial 候选和程序校验
- 风险假设：无版本化数据时无法判断后续检索改动是否有效或破坏权限/安全
- 关联：`docs/adr/0001-knowledge-base-permission.md`、`docs/adr/0002-official-vectorstore.md`、`evals/`

## 2. 目标与发布问题

- 要证明：案例可被 Schema、原文引用、重复和 split 规则程序校验
- 不在本轮证明：Recall/MRR/nDCG、生产检索质量、Reranker 价值
- 发布判断：本版本不设发布阈值；`RAG-005` 先跑基线

## 3. 数据集设计

| 数据集 | 来源 | 数量 | 脱敏 | 标签方法 | 版本 | 用途 |
|---|---|---|---|---|---|---|
| Silver | 合成北风/青禾语料 | 17 | 合成，无客户数据 | AI 生成，待独立校验 | 0.1.0 | 开发与扩展 |
| Adversarial | 同上 + 对抗切片 | 27 | 合成 | 规则 + 独立校验 | 0.1.0 | 权限/安全/冲突 |
| Gold | 人工从上述抽审 | 24（阿明，2026-09-13） | 合成 | 有权负责人 | 0.1.0 已冻结这 24 条 | 发布门禁候选 |
| Observed Regression | 无 | 0 | — | — | — | 无授权工单 |
| Smoke | 本语料可兼做链路夹具 | 不单独建 | 合成 | 规则 | 0.1.0 | 仅程序校验 |

划分：development 26 / validation 9 / holdout 9。Holdout 不参与后续调参。

## 4. Case 契约

见 `evals/schemas/rag_case.schema.json`。可回答案例必须有 `exact_quote`、`document_id`、`source`、`version`。

## 5. 指标与阈值

本任务不猜测阈值。`RAG-005` 用同一数据集实测 Recall@1/5/10、MRR、nDCG、Citation、Safety。

## 6. 评分器

- 本阶段：确定性 Schema/引用/重复/敏感扫描（`pytest tests/eval/ -v`）
- 下一阶段：检索指标与人工 Gold

## 7. 执行配置

- 语料：`evals/fixtures/corpus/`（`corpus-0.1.0`）
- VectorStore 政策：正式 Local（ADR-0002），本任务不建评测索引
- Embedding：不在本任务运行
- 命令：`pytest tests/eval/ -v`
