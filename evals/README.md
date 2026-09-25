# evals

> 状态：已预留，**尚无版本化 Evaluation 数据集**  
> 更新日期：2026-09-13

本目录将存放 `RAG-004` 之后的 Case Schema、Silver/Adversarial 候选和 Gold 集。空目录不能证明检索质量。

## 约束

- AI 生成数据默认是 Silver，不是 Gold。
- 未授权生产数据和密钥不得进入本目录。
- 没有冻结数据集和真实 Embedding 时，不得用 Mock 报告生产检索质量。
- 评测计划模板：`docs/templates/evaluation-plan-template.md`。
- 人类可读报告目录：`docs/evaluations/`。

进入条件：`RAG-001` 完成，且 `RAG-002` / `RAG-003` 的决策已记录（或明确声明本轮评测只使用不依赖未决权限/向量库决策的切片）。
