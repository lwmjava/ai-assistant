# Query Rewrite 进入条件复核（2026-11-10）

结论：**未触发**。在已有真实 Embedding 的 official 证据上，声明的进入条件「Recall@5 跌破 0.90」不成立。建议 1.0 内**不实施**查询改写，保持现有 HybridRetriever 行为；是否调整门槛或关闭/保留本条件卡，由用户明确决定范围处置。本次不编写改写实现，不改切分、Embedding 或 Reranker，也不把本卡自行标为 done。

复核时间：2026-11-10 11:16–11:40（Asia/Shanghai）。仓库 `E:\culture\SmartCustomerServiceSystem\ai-assistant`，分支起点 `codex/semantic-rerank-experiment` / `git 7a12329329b20f020227b3e49baa8dfa40ef7f84`。未 push。未新跑 `python scripts/run_rag_baseline.py --mode official`，以避免重复产生真实 Embedding 费用；最近一次同等配置的真实 official 检索结果已包含在当日语义重排实验对照中。

## 进入条件如何理解

AGENTS.md 第 14 节写明：Query Rewrite 进入条件为 Recall@5 跌破 0.90。本卡 `tasks.yaml` 既有事实把该指标指向冻结基线 `evals/reports/rag-v0.1-baseline-20260919.json` 的 `overall.recall`（约 0.971），并写明当时未跌破。

因此本次把**主证据**定为：真实 official、非 Mock、记录了 provider/模型/维度的 Recall@5，优先看 **overall**，并同时核对**不使用 holdout 的调参集**（development+validation / `decision_inputs.summary` / 语义重排报告中的 `tuning`）。任一切片低于 0.90 **不会单独触发**本卡，但会在下面如实列出，供用户决定是否改门槛。

Mock 或 smoke 结果只证明链路，不作为质量或进入条件证据。

## 采用的真实 official 证据

共同配置（凡下表采用的报告均满足）：provider `OpenAICompatibleEmbeddingProvider`，模型 `text-embedding-v3`，维度 1024，向量库 local，后端 native，切分 structured 500/64，数据集 `rag-eval@0.1.0`，检索深度与融合常数以各报告为准（当前生产对照为 rrf_k=60、depth=10）。

| 报告 | 运行时间 (UTC) | 代码版本 | rrf_k | overall Recall@5（35 可评分 / 44 条） | 调参集 Recall@5（28 可评分 / 35 条） | holdout Recall@5（仅记录） |
| --- | --- | --- | --- | --- | --- | --- |
| [冻结基线 20260919](../../evals/reports/rag-v0.1-baseline-20260919.json) | 2026-09-19 02:59:43 | `f52fa70cf5ab6e4b699560ac1f1b9c69df7230b8` | 60 | 0.9714 | 未单列（development 1.0 + validation 0.8333） | 1.0 |
| [当前官方基线 20261004](../../evals/reports/rag-v0.1-baseline-20261004.json) | 2026-10-04 18:11:59 | `ab72748019548418687f8ada19ad51a962289632` | 60 | 0.9619 | 0.9524（decision_inputs.summary） | 1.0 |
| [复跑官方基线 20261010](../../evals/reports/rag-v0.1-baseline-20261010.json) | 2026-10-10 15:00:16 | `1319c305065cc24bd3d7a9c199e99d4c5f83b459` | 60 | 0.9619 | 0.9524（decision_inputs.summary） | 1.0 |
| [语义重排官方对照 20261110](../../evals/reports/rag-v0.1-semantic-rerank-20261110.json) | 2026-11-10 02:47:05 | `git 507d21f6e57b083dc34316a33ff52b81f448a640` | 60 | 对照/处理均为 0.9714 | 对照/处理均为 0.9643 | 对照/处理均为 1.0 |

最新真实对照（2026-11-10，Windows 11 AMD64，Python 3.12.0，12 CPU，Intel Family 6 Model 158；索引版本 1，度量 cosine，归一化 l2；13 篇文档 / 37 个块）与冻结基线 overall Recall@5 同为 0.9714，调参集为 0.9643，均明显高于 0.90。2026-10-04 / 2026-10-10 官方基线 overall 为 0.9619、调参集为 0.9524，同样未跌破。

同系列 rrf_k=40/60/80 的 2026-09-23 / 2026-09-24 official 报告 overall 亦为 0.9619，不改变结论。词面覆盖实验 `rag-v0.1-lexical-coverage-20261002.json` 对照/处理 overall 为 0.9714、调参集为 0.9643，仅作旁证。

明确排除：`evals/reports/rag-v0.1-semantic-rerank-20261110-smoke.json`（MockEmbeddingProvider / mock-embedding / 64 维，`quality_evidence=false`）。

## 低于 0.90 的切片（不触发本卡）

在上述真实 official 基线中，**validation** 分片 Recall@5 为 0.8333（9 条中 6 条可评分），**effective_date** 类别为 0.6667。这是小样本切片，不是本卡既有事实使用的 overall 指标，也不是禁止用 holdout 调参时更应看的 development+validation 汇总（该汇总仍 ≥ 0.9524）。

若用户要把进入条件改成「任一验证切片跌破 0.90」，才需要另作范围决定；本次不按该解释实施改写。

## 范围处置建议

1. **建议不实施** Query Rewrite：进入条件未触发，1.0 内先保持现有检索，不增加改写层。
2. 代码现状与卡内事实一致：`app/` 下无 `query_rewrite` / `rewrite` 实现。本次也不新增。
3. 请用户在以下处置中明确选一项（实现手不得自行把本卡记为 done）：
   - 接受「未触发、不实施」的复核结论，保留本卡为条件任务，待后续真实 official overall 或调参集 Recall@5 跌破 0.90 再开工；或
   - 明确关闭/移出 1.0 范围；或
   - 明确改写进入条件（例如改为 validation 切片或类别门槛）并另行授权实施。
4. 若验收需要复跑 `python scripts/run_rag_baseline.py --mode official`，会再次产生真实 Embedding 费用；本复核未执行该命令。

结构化摘录见 `evals/reports/rag-v0.1-query-rewrite-gate-20261110.json`。
