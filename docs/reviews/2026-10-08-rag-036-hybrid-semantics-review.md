# RAG-036 混合语义（Milvus/Local BM25 全零分支）独立审查

审查人：未参与实施的独立审查 Agent
日期：2026-10-08
任务卡：tasks.yaml `RAG-036`
审查方式：对照原始契约逐文件阅读 + 自行运行测试 + 自行设计反例 + 核对证据指纹，不依赖实施者摘要。

## 1. 审查范围与文件清单

| 文件 | 审查重点 |
|---|---|
| `app/rag/vectorstore/milvus.py` | hybrid_search 全零分支（388-396）、候选窗口（357-385）、相似度留存、异常契约 |
| `app/rag/vectorstore/local.py` | 对照 BM25 全零处理、`_rrf` 实现、`_bm25_scores`/`_bm25_all_zero_reason` |
| `tests/test_rag_036_hybrid_semantics.py` | 全零断言强度、三种全零覆盖、双后端对称性、边界遗漏 |
| `scripts/rag_hybrid_semantics_check.py` | 7 切片对照、词面遗漏编码、指纹留存、密钥/连接串 |
| `scripts/rag_hybrid_embedding_check.py` | 预算守卫、向量复用指纹、是否隐式重试/泄密 |
| `docs/plans/implementation_rag_036_hybrid_semantics.md` | 边界记录诚实性、未做项 |
| 证据：`evals/reports/rag-036-*` | 红/绿/回归/真实 Milvus/真实 Embedding 报告与指纹 |

实际执行的独立动作：
- 独立运行 `pytest tests/test_rag_036_hybrid_semantics.py` → **17 passed in 3.23s**（退出码 0）。
- 自行运行 6 个反例脚本（`_rrf` 空列表、索引对齐、-0.0/极小负、混合分数、原因码、空候选），全部符合预期。
- `git diff HEAD~3 -- app/rag/vectorstore/` 确认本卡业务改动仅一行。
- 计算 5 个被审文件当前 SHA256，与报告内指纹比对。

## 2. 逐条审查发现

### 2.1 正确性 — 通过

- 全零分支实现为 `if any(s > 0 for s in bm25): sparse_order = argsort(...) else: sparse_order = []`（milvus.py 389-393；local.py 246-249 同构）。git diff 确认修复前是 `sparse_order = list(range(len(ordered)))`——即把自然装载顺序当成第二路排名。
- **红测证据属实**：`rag-036-zero-red-20261008.txt` 中修前实际值 doc0=0.032786885（=2/61）、doc1=0.032258065（=2/62），正是「稠密位置 + 同一位置的伪稀疏排名」叠加翻倍；修后期望 1/61、1/62。与实现说明数字一致。
- `_rrf([dense_order, []], k=60)` 我亲自验证：空 ranking 不产生任何 `fused` 项，结果恰为 `[(0,1/61),(1,1/62)]`，无意外分数。
- **-0.0 / 极小负 / 正数误判**：`_bm25_scores` 的 IDF 用 `log((n-freq+0.5)/(freq+0.5)+1.0)`，括号内恒 >1.0，故 IDF 恒为正；命中项分子 `f*(k1+1)>0`、分母 >0。得分只能是「精确 0.0」或「严格正数」，不存在负分或 -0.0 来源。我另用 `[-0.0, 0.0, -1e-15]` 直接喂 `any(s>0)`，结果为 False，边界安全。
- **混合分数**（一正两零）：我实测 `_bm25_scores(['apple'], [['apple'],[],['pear']]) = [0.8007, 0.0, 0.0]`，`any>0=True`，仍进稀疏路且正分文档排前——未被全零分支误吞。

### 2.2 边界处理 — 通过（含 1 条观察）

- 候选空早退在 BM25 之前：milvus.py 361 行 `if not candidate_ids: return []` 位于 388 行 `_bm25_scores` 之前。SQL 收窄后 `ordered` 为空时，`_bm25_scores(..., [])=[]`、两路均空、`fused=[]`，不报错返回 `[]`。
- **索引对齐**：`ordered` 保持 `candidate_ids` 的距离顺序；`dense_order=range(len(ordered))` 与 `sparse_order=argsort(-bm25)` 都是 `ordered` 的下标；`_rrf` 在候选下标空间融合，`row=ordered[idx]`。我用 `_rrf([[0,1,2],[2,0]],60)` 验证 idx 映射正确，无错位。
- 相似度按 id 留存（`similarity_by_id`，360 行），SQL 收窄后仍能取回真实 COSINE，`ordered` 里取不到时回落 0.0。
- **观察（非缺陷）**：local.py 在全零分支额外打结构化日志 `bm25_all_zero reason=...`（250-259 行），而 milvus.py 全零分支**只置空列表、不打原因日志**。生产用 Milvus 后端时，BM25 为何退出融合缺少可观测信号。不影响正确性，记为警告。

### 2.3 非目标遵守 — 通过

- **未改 RRF 常数**：`_rrf` 本体未动；两处 `hybrid_search` 签名 `rrf_k: int = 60` 不变；调用仍 `_rrf([...], k=rrf_k)`。测试用 1/61=1/(60+1) 锁定 k，是好守护。
- **未引入独立稀疏召回**：milvus 仍只打一次 `_remote_search`（稠密），BM25 在稠密候选窗口内本地计算；没有新增 Milvus 稀疏查询通路。
- **未改窗口**：`expand = max(top_k*4, 20)` 未在 diff 中出现；脚本报告 `dense_window=20` 一致。
- diff 中其余 `validate_add`/`_write_target`/删除 `target_index_id` 重建路径确属 RAG-032，实现说明已显式切割，不混算本卡。

### 2.4 测试有效性 — 通过

- `test_all_zero_bm25_has_only_dense_contribution` 参数化三种全零（空查询词 / 空文档词 / 无交集），且对 local 与 milvus 双后端各跑一遍，满足对称性。
- milvus 用例 monkeypatch 的只是 `_remote_search`（pymilvus 调用本身），357 行之后的 SQL 收窄、tokens 解析、BM25、全零分支、`_rrf` 全部是真实生产代码——确实测到了 Milvus 分支，不是只测结果列表。
- 断言 `score == approx([1/61,1/62])` 是精确 RRF 分数守护：若未来有人把稀疏路改回非空、或 k 变动，测试会挂。
- 预算测试覆盖耗尽持久化、失败尝试留存、unknown_usage/超预占/非法类型（True/-1/"44"/[44]/1000）拒绝；向量指纹测试覆盖 6 类篡改（vectors/report/model/dimension/corpus/dataset）。质量高。
- 我未发现被遗漏的关键边界：混合正分已由脚本 `positive_overlap` 切片 + 我的反例 4 覆盖；空候选由代码路径自保。

### 2.5 安全 / 合规 — 通过

- 两脚本无硬编码密钥；`EMBEDDING_API_KEY` 来自 settings，报告只记录 `request_id` 与模型名，不记 token。
- embedding 脚本硬编码端点白名单（https + dashscope.aliyuncs.com）、模型 `text-embedding-v3`、维度 1024；`retries=0` 无隐式重试；超时 30s。
- 预算在 HTTP 之前用 `BEGIN IMMEDIATE` 事务预占（20 次 / 100k tokens / 0.10 元），失败不退款；真实报告仅 1 次 HTTP、reserved 300 / usage 44 tokens。
- 报告字段为 collection 名（生成哈希）、server_version、路径，无 Milvus URI/token、无连接串泄露。

### 2.6 文档诚实性 — 通过

- 实现说明明确区分「固定向量证明语义」与「真实模型证明链路」，不声称总体质量；
- 如实记录 Milvus 与 Local 的 BM25 候选宇宙不同、纯关键词可漏、窗口可被过滤耗尽；
- 明确「独立稀疏召回须另 ADR、资源 ACL 仍 Planned、默认 local / Milvus Partial 不变」；
- 主动切割 RAG-032 改动、声明 mypy 仅跑 vectorstore/ 非全 app、ruff 首次沙箱失败后在沙箱外复跑；
- 未把 Planned 写成 Implemented，未用 Mock 宣称质量提升。

## 3. 我设计的反例与代码防御结果

| 反例 | 预期 | 实测 | 是否防住 |
|---|---|---|---|
| `_rrf([[0,1],[]],60)` 空稀疏路 | 仅 dense 贡献 1/61、1/62 | 恰为该值 | 是 |
| `-0.0 / -1e-15` 喂 `any(s>0)` | 判为全零 | False | 是（且 BM25 本不产生负分） |
| 一正两零混合 bm25 | 仍进稀疏路 | `any>0=True`，正分排前 | 是 |
| `ordered` 被 SQL 收窄为空 | 不崩、返回 [] | 两路空、fused=[] | 是 |
| 索引空间错位（dense/sparse 下标） | fused idx 正确映射 ordered | `_rrf([[0,1,2],[2,0]])` 映射正确 | 是 |
| 报告指纹被篡改 | 复用向量被拒 | 6 类篡改均 raise | 是（测试覆盖） |

## 4. 四条验收标准独立判定

1. **全零不造排名** — **通过**。红/绿证据 + 双后端单测 + 我的反例 1/3 一致。
2. **纯关键词遗漏报告** — **通过**。`keyword_outside_window`（Local 命中 keyword-04 / Milvus 只回 dense-00）与 `filtered_window_exhausted`（Local 回 allowed / Milvus 空）两个切片把差异**编码进断言**而非掩盖，并写入 limitations。
3. **真实 Milvus 切片可复现** — **通过（附小注）**。报告 `real_milvus=true`、server 2.5.11、全新 collection、代码/数据集指纹齐全，复验走 `--vectors` 零费用。当前代码指纹（milvus.py=eecb53…、local.py=905d…、semantics_check=8434…）与最终 `real-embedding-verified-reuse` 报告一致。我无法本机另起 Milvus，但零费用复验路径与指纹闭环成立。
4. **新增召回架构另 ADR** — **通过（观察项）**。本卡未引入新召回架构；文档已声明独立稀疏召回需另 ADR。一行规则修复本就不需要新 ADR。

## 5. 非目标遵守判定

- 不改成独立稀疏召回：**遵守**。
- 不改 RRF：**遵守**（k=60 未动，`_rrf` 未改）。
- 附带确认：未改窗口大小、未改默认向量库、未声称 Milvus 升 Implemented。

## 6. 警告项（非阻塞）

- **W1（可观测性不对称）**：Milvus 全零分支缺少 Local 已有的 `bm25_all_zero reason=...` 结构化日志。建议后续补一行同构日志，便于生产定位 BM25 退出原因；不构成本卡关闭阻塞。
- **W2（指纹小漂移，已闭环）**：7 切片固定向量真实 Milvus 记录（`fixed-real-milvus-final`）里 milvus.py 指纹为 `8ebd40…`，当前为 `eecb53…`；最终 `real-embedding-verified-reuse` 报告指纹与当前代码一致。两次之间 milvus.py 的差异落在 RAG-032 的 `validate_add`/删除重建区，全零融合行未变，故语义证据仍有效；若要彻底闭环，可在当前代码上零费用重跑一次 7 切片脚本。

## 7. 结论

**同意本卡从代码质量、边界、测试与非目标维度关闭。** 本卡业务修复为一行、最小且正确；测试真断言了分数而非列表；双后端对称；红/绿/真实服务/真实模型证据链完整且无密钥泄露；文档诚实。无「问题」级发现。

遗留的关闭门禁不在 RAG-036 自身：实现说明已声明需待 RAG-032 重建/回退整改与 RAG-015 前置复验。这两项属于跨卡前置条件，不属于本卡缺陷——主 Agent 在汇总翻转 done 前应确认这两条前置已落地，而非要求 RAG-036 再补代码。
