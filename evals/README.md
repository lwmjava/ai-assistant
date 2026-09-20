# evals

> 状态：`RAG-004` 已建立 Schema、合成数据集和 **Gold v0.1（24 条）**；
> `RAG-005` 已产出 **首份真实嵌入检索基线（2026-09-19）**
> 更新日期：2026-09-20

## 当前资产

| 路径 | 说明 |
|---|---|
| `evals/schemas/rag_case.schema.json` | Case JSON Schema |
| `evals/fixtures/corpus/` | 授权合成语料 `corpus-0.1.0`（13 篇 / 37 分块） |
| `evals/datasets/rag-v0.1/cases.json` | 44 条；其中 24 条 Gold v0.1 |
| `evals/datasets/rag-v0.1/index.json` | 版本、划分和 Gold 状态 |
| `evals/reports/` | 基线运行的机器可读报告 |
| `tests/eval/validation.py` | Schema/引用/重复/敏感扫描 |
| `tests/eval/metrics.py` | Recall/MRR/nDCG/分位数/引用命中纯函数 |
| `tests/eval/harness.py` | 语料摄取进隔离索引并跑真实检索链路 |
| `scripts/run_rag_baseline.py` | 基线运行器 |

## 划分

- development：26
- validation：9
- holdout：9（`RAG-005` 基线已运行一次，此后不得用于调参）

## 运行

```powershell
python scripts/run_rag_baseline.py --mode official   # 真实嵌入，正式基线
python scripts/run_rag_baseline.py --mode smoke      # Mock 嵌入，仅验链路
pytest tests/eval/ -v                                # 数据集校验 + 指标 + 夹具冒烟
```

正式模式在解析到 Mock 嵌入或 `RAG_VECTOR_STORE != local` 时直接退出，不产出报告。
评测索引写在 `data/eval_rag_v01.db`（每次重建，已 gitignore），不污染开发库与测试库。

## 约束

- AI 生成数据默认是 Silver。Gold v0.1 已由阿明于 2026-09-13 批准 24 条。
- 未授权生产数据和密钥不得进入本目录。当前语料全部合成。
- Mock 嵌入只能证明链路，不得用于报告检索质量。
- 当前基线的有效性限制（语料规模、未测生成层）见
  `docs/evaluations/rag-v0.1-baseline-report.md` 第 6、7 节，引用分数时必须一并说明。
- 后续优化遵守单变量原则：一次只改 Chunking、Embedding 或 Rerank 中的一项，并与本基线对比。
- 2026-09-19 A1：同租户当前文档被检索不记越权；资源 ACL（`acl_planned`）只约束生成层答案点。
- ADR-0003 已 Accepted（选项 A）。`RAG_EFFECTIVE_DATE_FILTER` 默认关闭；打开后带未来日期的查询才检索 scheduled 预告。
- 不得改写 `evals/reports/rag-v0.1-baseline-20260919.json`。
- `RAG-006` 读路径默认 `RAG_KB_SCOPE=tenant`。脚本化 LLM 已覆盖注入文档拒工具；真实 LLM 拒答/Citation/`forbidden_answer_points` 未测。
