# rag-v0.1 审核链、分级定义、证据口径与泄漏判定（RAG-034）

建立日期：2026-10-07
适用范围：`evals/datasets/rag-v0.1/`、`evals/fixtures/corpus/`、`evals/reports/`
核对基线：HEAD `cfafcdb`（分支 `rag-batch-20261007`）

本文件是 RAG-034 的交付主体。它不改动冻结基线，也不把 AI 生成内容自动升级为 Gold；
只把「审核链怎么走、什么算 Gold、什么算有原文证据、什么算泄漏」写成可执行口径，
并据此处置缺证据的条目。

---

## 1. 审核链

```
语料 manifest（授权合成）
  └─> 生成器 generator=rag-dataset-v1 / RAG-004-session
        └─> 独立复核（docs/evaluations/rag-v0.1-independent-review.md）
              └─> 人工 Gold 审核（阿明，2026-09-13，逐条记于 gold-review-batch.md）
                    └─> 版本登记（evals/datasets/rag-v0.1/index.json + evals/VERSIONS.md）
                          └─> 程序校验（tests/eval/validation.py，每次 CI 复跑）
                                └─> 基线运行（scripts/run_rag_baseline.py --mode official）
```

每一环的产物与责任：

| 环 | 产物 | 责任 | 现状 |
|---|---|---|---|
| 语料授权 | `evals/fixtures/corpus/manifest.json`（`authorized: true`） | 语料必须是仓库内合成夹具 | ✅ 13 篇全合成，无生产数据 |
| 生成 | `cases.json[].provenance`（generator / prompt_version / generated_at） | 记录来源，AI 生成默认 Silver | ✅ 44 条全有 |
| 独立复核 | `rag-v0.1-independent-review.md` | 非生成者复核；自述限制「同一会话复核不能代替人工 Gold」 | ✅ 存在；对 rag-036~038 判 HUMAN_REVIEW |
| 人工 Gold | `cases.json[].review` + `gold-review-batch.md` | 逐条人工确认 | ⚠️ 见 §2 |
| 版本登记 | `index.json` + `evals/VERSIONS.md` | 数据集/语料/报告三处版本互相可追溯 | ✅ RAG-034 新建 `evals/VERSIONS.md`（含权威基线声明） |
| 程序校验 | `tests/eval/validation.py` | 口径可复跑 | ⚠️ RAG-034 补 4 条 |
| 基线运行 | `scripts/run_rag_baseline.py` | official 才可作质量结论 | ✅ smoke 明确不可作采纳证据 |

### 审核链的已知粒度缺口

- 原 24 条 Gold 中 23 条的 `review.notes` 是**同一句话**（「产品负责人确认 24 条 Gold v0.1。」），
  只有 rag-036 有个性化理由。**逐条审核理由未区分**，无法追溯「为什么这一条被判 Gold」。
  （降级后当前 Gold 为 21 条，缺口仍在。）
- `gold-review-batch.md` 第 43–46 行的「审核记录」表只有 **2 行聚合记录**，不是逐条。
- 这两点属于审核链可追溯性缺口，**不在本卡消除**（需要人工逐条补写理由），在此登记。

---

## 2. 分级定义

| 档 | 定义 | 判定字段 | 晋升条件 | 降级条件 |
|---|---|---|---|---|
| **Gold** | 经人工逐条确认、且具备可核验原文证据的用例 | `provenance.review_status == "gold"` 且 `review.decision ∈ {ACCEPT_GOLD, REVISE_AND_ACCEPT}` | 人工审核 + 满足 §3 证据口径 + 记入 `index.gold_case_ids` | 不满足 §3 任一口径 → `decision=KEEP_SILVER`、`review_status=silver` |
| **Silver** | AI 生成、未经人工确认或已被降级 | `provenance.review_status == "silver"` / `"unreviewed"`，或 `tier == "silver"` | 人工补审通过后可提名 Gold | — |
| **Adversarial** | 攻击/边界形态（注入、越权、冲突、过期、无答案、引用错配） | `tier == "adversarial"` | 与 Gold 是**正交维度**，adversarial 也可以是 Gold | — |
| **Regression** | 已发现并修复的缺陷固化成回归用例，用于防复发 | **当前无字段、无数据、无运行入口** | 需先有授权工单 | — |
| **Smoke** | 只验链路不验质量的最小用例集 | **当前只有运行模式语义**（`--mode smoke`），无数据集级定义 | — | — |

### 关键澄清（避免误读）

- **Gold 不是第三档 tier，两者是正交维度**。`tier` 枚举只有 `silver` / `adversarial`
  （schema 定义），Gold 叠加在 `provenance.review_status` 上。
  实测原 24 条 Gold 的 tier 分布为 silver 7 + adversarial 17。
- **`index.json` 的 `counts` 只统计 `tier`，不含 Gold**。所以 `counts.silver=17`、
  `counts.adversarial=27`（合计 44）与 `gold_count=24` **不冲突、不是重复计数**：
  前者按 tier 切，后者按 review_status 切，一条用例可以同时落在两边。
- `counts` 里没有 `gold` 键，读 `index.json` 只看 `counts` 会看不到 Gold 分层 —— 这是表达缺口。
- 降级后 `gold_count` 为 **21**，`counts` 不受影响（降的是 review_status，不是 tier）。
- **Regression 与 Smoke 当前只有计划表各一行**（`rag-v0.1-evaluation-plan.md:27-28`：
  Regression「无 / 0」、Smoke「不单独建」）。本卡**不新建**这两档数据（需要授权工单与质量结论），
  只在此明确登记其状态，避免后续误以为已存在。

---

## 3. 原文证据口径（本卡核心，逐条可执行）

「有原文证据」不是只看 `expected_evidence` 是否非空——拒答类用例按设计就没有正证据。
按 `should_answer` 分三种形态，**满足其一即视为具备**：

| 形态 | 适用 | 要求 |
|---|---|---|
| **A. 正证据** | `should_answer == true` | ≥1 条 `expected_evidence`，`exact_quote` 可在 `source` 文件逐字定位，`document_id` 在 manifest 中，version / effective_at 与 manifest 一致 |
| **B. 拒答但引用了资料** | `should_answer == false` 且 `expected_evidence` 非空 | 同 A 的逐字定位要求（例：rag-022 conflict、rag-024 ambiguous、rag-040 prompt_injection） |
| **C. 拒答且无引用** | `should_answer == false` 且 `expected_evidence` 为空 | 必须有**替代依据**，二选一：<br>**C1**：`forbidden_document_ids` 非空且文档真实存在于 manifest（反向证据，例：rag-033/034/035 cross_tenant）<br>**C2**：`notes` 非空且说明「语料为何不支持」或「依据哪条已批准裁决」（例：rag-017 说明语料范围、rag-036 引 ADR-0001 选项 A） |

**补充口径 D（一致性）**：`expected_document_ids` 中声明的每一个文档，都必须在
`expected_evidence` 中有对应条目。声明了却无引文 = 该声明不可核验。

### 处置结果（按上述口径逐条核对 24 条 Gold）

| 口径 | 违规条目 | 处置 |
|---|---|---|
| C（既无 forbidden 也无 notes） | **rag-019、rag-020**（no_answer） | **降级为 Silver**（`decision=KEEP_SILVER`），待人工补审后重新提名 |
| D（声明文档缺引文） | **rag-041**（声明 doc-escalation-current，只有 doc-injection-printer 的引文） | **降级为 Silver**，待人工补引文或修正声明 |
| — | rag-017 / rag-036 | **保留 Gold**：notes 已给出依据（语料范围 / ADR-0001 选项 A），符合 C2 |
| — | rag-033 / rag-034 / rag-035 | **保留 Gold**：forbidden 指向 manifest 中真实存在的文档，符合 C1 |

Gold 数量：24 → **21**。`index.json` 同步更新 `gold_count`、`gold_case_ids`，
并新增 `downgrade_log` 记录每一条的降级原因与口径。

**未做「补审」**：补审须由人工审核人（阿明）执行，Agent 不得代写审核结论；
也不得由 Agent 自动生成引文补进 Gold（违反 non_goals「不把 AI 生成自动升级 Gold」）。

---

## 4. 泄漏判定口径

`splits` 现状：development 26 / validation 9 / holdout 9（合计 44，与声明一致）。

| 判定项 | 是否算泄漏 | 现状 | 程序校验 |
|---|---|---|---|
| 同一归一化 query 出现在 >1 个 split | **是** | 0 条 | ✅ 已有 `SPLIT_LEAK` |
| 同 category+tenant+user 的 query Jaccard ≥ 0.85 | **是** | 0 条 | ✅ 已有 `NEAR_DUPLICATE` |
| `expected_answer_points` 跨 split 重复 | **是** | 0 处（rag-005/rag-014 同在 development，不跨） | RAG-034 新增 `SPLIT_ANSWER_POINT_LEAK` |
| 同一 `exact_quote` 被不同 split 的用例引用 | **否** | **9 组**（涉及 26 条证据） | 见下方说明 |
| 同一 `(document_id, section)` 跨 split 复用 | **否** | **9 组**（涉及 33 条证据） | 见下方说明 |

口径说明：上表一律用「**组数**」= 同一个引文 / 段落出现在多少个不同的 split 分组里，
不是「涉及的证据条数」。两种口径数字不同（9 组引文实际涉及 26 条证据），
混用会让「只有 9 条有复用」被读成「只有 9 条证据受影响」。
| chunk 级复用 | 无法判定 | `chunk_id` 全为 null（49 条 evidence） | 登记为缺口 |

### 为什么引文/段落级跨 split 复用不算泄漏

语料只有 **13 篇**且主题高度重叠（同一篇计费手册同时是「月费」「发票」「消息额度」
三类问题的唯一来源）。在这种规模下禁止跨 split 引用同一文档或同一引文，等于无法划分
三个 split。泄漏的本质是**评估信号外泄**——即模型能从 validation/holdout 的题干或答案
反推 development 的答案；共享同一份语料不构成这种外泄，因为语料本身对全部 split 都是
同一份、不是被预测的对象。

因此本卡明确：泄漏判定**只到 query 与 answer point 级**，引文/段落级复用**登记但不判失败**。
这一点必须写明，否则「已做泄漏检查」会被误读为「已做到 chunk 级」。

### 已知缺口

- **chunk 级泄漏无法检测**：全部 49 条 `expected_evidence` 的 `chunk_id` 均为 `null`、
  `page` 均为 `null`，证据粒度只到 `document + section + exact_quote`。
  补齐 chunk 级标识属数据增强，不在本卡（L0 文档卡）范围。
- 语料分块数 37 来自分块器运行输出（`run_config.indexed_chunks`），
  `manifest.json` **没有 chunks 字段**，无法静态核实。

---

## 5. 版本登记

统一登记表新建在 **`evals/VERSIONS.md`**，覆盖数据集侧、语料侧、运行侧三处版本，
并明文声明权威基线：

- **冻结基线**：`evals/reports/rag-v0.1-baseline-20260919.json`（多处文档与
  `scripts/run_rag_baseline.py:110-121 refuse_frozen()` 双重保护，不得改写）
- **当前 official 基线**：`evals/reports/rag-v0.1-baseline-20261004.json`
  （与冻结基线口径一致：official / text-embedding-v3 / local / native / structured 500-64 /
  rrf_k=60 / depth=10；指标不同，用于对比，**不覆盖**冻结基线）

此前仓库里**没有任何文件明文声明哪一份是权威基线**，两份并存且指标不同
（20260919 Recall@1=0.7952 / MRR=0.9286；20261004 Recall@1=0.8095 / MRR=0.9429），
容易误引。本卡在 VERSIONS.md 中固定该声明。

**已知缺口**：rag-v0.1 的报告**没有 `dataset_hash` / `prompt_hash`** 字段
（对比：agent-chain 报告两者都有），报告与数据集之间只有版本号字符串、无哈希绑定。
本卡在 VERSIONS.md 中补记数据集内容 sha256 作为手工绑定，字段级绑定属数据改造，不在本卡。

---

## 6. 本卡新增的程序校验（`tests/eval/validation.py`）

| Finding code | 校验内容 | 对应口径 |
|---|---|---|
| `GOLD_EVIDENCE_BASIS` | `should_answer=false` 且无 `expected_evidence` 的 Gold，必须有非空 `forbidden_document_ids` 或非空 `notes` | §3 C |
| `EXPECTED_DOC_WITHOUT_EVIDENCE` | `expected_document_ids` 中每个文档都必须在 `expected_evidence` 有对应 | §3 D |
| `SPLIT_ANSWER_POINT_LEAK` | 同一 `expected_answer_points` 条目不得出现在 >1 个 split | §4 |
| `VERSION_REGISTRY_MISMATCH` | `index.json` 与 corpus `manifest.json` 的 `corpus_id` / `corpus_version` 必须一致；`source_snapshot` 必须等于 `corpus-<corpus_version>` | §5 |

前两条在降级完成后应**零 finding**；后两条为守护项，当前亦应为零 finding。

### 数据集级测试新增 2 条（`tests/eval/test_rag_dataset_v01.py`）

| 用例 | 守住什么 |
|---|---|
| `test_downgraded_cases_carry_a_traceable_decision` | 降级必须留记录：`downgrade_log` 与 `review_status` / `review.decision` / `notes` 四处一致，且 `review.notes` **不得**混入 Agent 写的说明，防止「悄悄改回 Gold」或「污染人工审核记录」 |
| `test_version_registry_declares_an_authoritative_baseline` | `evals/VERSIONS.md` 必须存在且指名冻结基线，防止两份 baseline 并存却没人说清谁是权威 |

变异验证（改数据 → 看测试是否变红 → 还原）：
M3 把 rag-041 悄悄改回 Gold 并删掉降级记录 → 3 条失败；
M4 去掉 VERSIONS.md 的权威基线声明 → 1 条失败。

### 复审后的修正（第二轮）

| 复审发现 | 修正 |
|---|---|
| **High**：`GOLD_EVIDENCE_BASIS` 把 `review.notes` 也算作证据依据 —— 而降级理由正是 Agent 写进 `review.notes` 的，等于「Agent 自己写的说明」被当成人工证据，口径 C 空转 | 依据只看用例级 `notes`；三条降级用例的 `review.notes` 还原为原始人工记录原文，降级理由改放 `notes` + `downgrade_log`；新增断言禁止 `review.notes` 出现 `RAG-034` 字样 |
| **Medium**：§4 的「8 组 / 6 组」与实际不符 | 复算后更正为 **9 组 / 9 组**，并写明「组数 ≠ 涉及证据条数」 |
| **Medium**：`side_effect: documentation-only` 与实际改了数据不符 | 在 `tasks.yaml` 的 progress 里登记实际范围（数据降级 + 校验规则），未改状态枚举 |
| Low：§4 数字口径、VERSIONS.md 哈希前提、两份基线差异归因、冻结基线未改动 | 逐条补写，见各节 |

**冻结基线未改动**：`evals/reports/rag-v0.1-baseline-20260919.json` 与 `20261004.json`
均未被本卡修改（`git status` 无这两个文件；两者 sha256 未变，可自行复核）。

---

## 7. 本卡边界：什么没做

| 未做项 | 原因 |
|---|---|
| 给 3 条降级用例补审 / 补引文 | 补审须人工审核人执行；Agent 自动生成引文顶回 Gold 违反 non_goals |
| 补写逐条审核理由（`review.notes` 23 条是同一句） | 需人工逐条写，已登记为审核链粒度缺口（§1） |
| 新建 Regression / Smoke 档数据 | 需授权工单与质量结论（§2） |
| 报告加 `dataset_hash` / `prompt_hash` 字段 | 属数据改造，本卡只做手工绑定（§5） |
| 把 Mock Embedding 的 Recall 当上线证据 | non_goals；基线用的是 text-embedding-v3，非 Mock |
