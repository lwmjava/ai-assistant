# evals

> 状态：`RAG-004` 已建立 Schema、合成数据集和 **Gold v0.1（24 条）**；**尚无 baseline 分数**
> 更新日期：2026-09-13

## 当前资产

| 路径 | 说明 |
|---|---|
| `evals/schemas/rag_case.schema.json` | Case JSON Schema |
| `evals/fixtures/corpus/` | 授权合成语料 `corpus-0.1.0` |
| `evals/datasets/rag-v0.1/cases.json` | 44 条；其中 24 条 Gold v0.1 |
| `evals/datasets/rag-v0.1/index.json` | 版本、划分和 Gold 状态 |
| `tests/eval/` | Schema/引用/重复/敏感扫描 |

## 划分

- development：26
- validation：9
- holdout：9（不得用于后续调参）

## 约束

- AI 生成数据默认是 Silver。Gold v0.1 已由阿明于 2026-09-13 批准 24 条，`gold_status=approved_v0.1`。
- 未授权生产数据和密钥不得进入本目录。当前语料全部合成。
- 没有冻结 Gold 和真实 Embedding 时，不得用 Mock 报告生产检索质量。
- 评测计划：`docs/evaluations/rag-v0.1-evaluation-plan.md`。
- 人类可读报告：`docs/evaluations/`。
- 校验命令：`pytest tests/eval/ -v`。

`RAG-005` 才能跑 Recall/MRR/nDCG 基线。本目录空分数不能证明检索质量。
