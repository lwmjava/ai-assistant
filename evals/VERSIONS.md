# 评测版本登记（rag-v0.1）

建立日期：2026-10-07（RAG-034）
维护规则：**改数据或换基线都要改这张表**，否则「用过哪份数据、跑的哪条口径」只能靠猜。

本表覆盖三处版本——数据集侧、语料侧、运行侧——并明文指定权威基线。
此前仓库里没有任何文件声明哪份报告是权威基线，两份 baseline 并存且指标不同，容易被误引。

---

## 1. 数据集侧

| 项 | 值 | 来源 |
|---|---|---|
| `dataset_id` | `rag-eval` | `evals/datasets/rag-v0.1/index.json`；两份报告 `run_config.dataset_id` 一致 |
| `dataset_version` | `0.1.0` | `index.json` |
| `corpus_id` | `northwind-demo-kb` | `index.json` 与 `evals/fixtures/corpus/manifest.json` **必须一致**（`VERSION_REGISTRY_MISMATCH` 校验） |
| `corpus_version` | `0.1.0` | 同上 |
| `source_snapshot` | `corpus-0.1.0` | 必须等于 `corpus-<corpus_version>` |
| `gold_status` | `approved_v0.1` | 人工 Gold 批次已批准 |
| `gold_count` | **21** | 2026-10-07 由 24 降为 21，见 §4 |
| 用例总数 | 44 | development 26 / validation 9 / holdout 9 |

内容指纹（手工绑定，见 §5 已知缺口）：

| 文件 | sha256（前 32 位） |
|---|---|
| `evals/datasets/rag-v0.1/cases.json` | `2b1d4de3c69fe456...` |
| `evals/datasets/rag-v0.1/index.json` | `f23517b82e4552c0...` |

**前提**：上面两个哈希是 **2026-10-07 RAG-034 降级（Gold 24→21）之后**的取值。
**`cases.json` 或 `index.json` 一改，这两行必须重算**，否则它们会从「绑定」变成误导。

半截哈希只用于人工核对，不是机器门禁——仓库里没有校验它的测试，别把它当防篡改。

---

## 2. 语料侧

| 项 | 值 | 来源 |
|---|---|---|
| `corpus_id` | `northwind-demo-kb` | `evals/fixtures/corpus/manifest.json` |
| `corpus_version` | `0.1.0` | 同上 |
| `authorized` | `true` | 全合成夹具，无生产数据 |
| 文档数 | 13 | `manifest.json.documents`；与报告 `indexed_documents=13` 一致 |
| 分块数 | 37 | **来自报告 `run_config.indexed_chunks`，不是 manifest** |

分块数记不进 manifest：`manifest.json` 没有 `chunks` 字段，37 这个数是分块器跑出来的，
静态核不了。这条缺口登记在此，避免下次有人去 manifest 里找 chunks。

---

## 3. 运行侧

两份 baseline 的**运行口径完全一致**（official / text-embedding-v3 / local / native /
structured 500-64 / rrf_k=60 / depth=10），可以直接对比；指标不同是因为代码演进，
不是换了口径。

| 项 | 20260919 | 20261004 |
|---|---|---|
| 文件 | `evals/reports/rag-v0.1-baseline-20260919.json` | `evals/reports/rag-v0.1-baseline-20261004.json` |
| 角色 | **冻结基线（权威）** | 当前 official 基线 |
| `mode` | official | official |
| `run_at` | 2026-09-19T02:59:43Z | 2026-10-04T18:11:59Z |
| `git_commit` | `f52fa70cf5ab6e4b699560ac1f1b9c69df7230b8` | `ab72748019548418687f8ada19ad51a962289632` |
| Embedding | `OpenAICompatibleEmbeddingProvider` / `text-embedding-v3` / 1024 维 | 同左 |
| 向量库 / 后端 | `local` / `native` | 同左 |
| 切分 | structured 500 / overlap 64 | 同左 |
| rrf_k / depth | 60 / 10 | 同左 |
| 用例数 | 44（可打分 35） | 44（可打分 35） |
| Recall@1 | 0.7952 | 0.8095 |
| Recall@5 | 0.9714 | 0.9619 |
| Recall@10 | 0.9714 | 0.9619 |
| MRR | 0.9286 | 0.9429 |
| nDCG@10 | 0.9416 | 0.9401 |
| quote_coverage | 0.9184（49 条证据命中 45） | 0.9184（同） |
| 安全违规 | 3 条（rag-026 / rag-036 / rag-037），违规率 0.1875 | 0 条，违规率 0 |
| 延迟 p50 / p95 | 531.3 / 612.8 ms | 291.4 / 342.3 ms |

### 权威基线声明

- **冻结基线 = `rag-v0.1-baseline-20260919.json`**，不得改写。双重保护：
  多处文档引用 + `scripts/run_rag_baseline.py` 的 `refuse_frozen()`（约 110-121 行）。
- `rag-v0.1-baseline-20261004.json` 是**当前 official 基线**，用于对比与持续观测，
  **不覆盖、不取代**冻结基线。
- 需要引用「rag-v0.1 基线指标」时，默认引冻结基线；引 20261004 必须写明日期，
  否则两个 Recall@1（0.7952 / 0.8095）会被混着用。

两份报告的安全违规不同（3 条 → 0 条）。**这不是评测口径变化**，
两份的 embedding / 切分 / rrf_k / depth / 用例集完全一致。
本表**未逐条复算归因**，不把它写成已证实的因果；引用时只说「违规数与读范围/权限类
改动同向」，需要因果结论请单独做归因核对。

---

## 4. Gold 变更记录

| 日期 | 变更 | 条数 | 依据 |
|---|---|---|---|
| 2026-09-13 | 人工审核批准 24 条 Gold v0.1 | 24 | `gold-review-batch.md` |
| 2026-10-07 | RAG-034 证据口径核对，3 条降级为 Silver | **21** | `docs/evaluations/rag-v0.1-审核链与分级定义.md` §3；逐条原因见 `index.json.downgrade_log` |

降级条目：`rag-019`、`rag-020`（拒答但既无反向证据也无说明，口径 C 不成立）、
`rag-041`（声明 `doc-escalation-current` 却无对应引文，口径 D 不成立）。

**只降级、不补审。** 补审要人工审核人执行；Agent 不得代写审核结论，
也不得自动生成引文把 Silver 顶回 Gold。

---

## 5. 已知缺口

1. **报告与数据集之间无哈希绑定**：rag-v0.1 的两份报告都**没有** `dataset_hash` /
   `prompt_hash` 字段（对比：agent-chain 报告两者都有），只有版本号字符串。
   §1 的 sha256 是手工补的绑定，字段级绑定属数据改造，不在 RAG-034 范围。
2. **manifest 无 chunks 字段**，分块数只能从报告 `run_config` 反查（见 §2）。
3. **报告不可复现校验**：两份报告的 `git_commit` 都记了，但没有脚本校验
   「当前代码 + 该 commit 的数据集 = 报告里的指标」。要断言可复现得实跑，
   属 RAG-017（CI 门禁）范围。

---

## 6. 其他评测产物（非 rag-v0.1 基线）

同目录下的实验性报告**不作质量结论**，仅用于选型对比：

| 文件族 | 用途 |
|---|---|
| `rag-v0.1-rrf-k{40,60,80}-*.json`、`rag-v0.1-rrf-k-comparison-*.json` | rrf_k 参数选型 |
| `rag-v0.1-lexical-coverage-20261002.json` | 词法覆盖观测 |
| `agent-chain-v0.1-*.json` | Agent 链路评测，**有** `dataset_hash` / `prompt_hash` |
| `*-smoke-*.json` | smoke 模式只验链路，**不可作采纳证据** |
