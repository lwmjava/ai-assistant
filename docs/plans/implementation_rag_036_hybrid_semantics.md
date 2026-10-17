# 跨向量库混合语义实现说明

日期：2026-10-08；任务 RAG-036。当前状态：**已关闭**。实现、隔离切片证据、独立验证与独立审查均已交付；RAG-032 前置整改已 done，RAG-015 写侧闭环为独立后续工作，不构成本卡读侧验收的硬阻塞。

## 实际行为与边界

授权检索者使用 Milvus 时，空查询词、候选词项全空或无词项交集都仅贡献 Dense RRF 分数。原来全零 BM25 会把自然顺序作为稀疏排名，造成分数翻倍；现在全零分支为 `sparse_order=[]`。存在正词面分数时仍保留原双路融合。失败仍沿用既有异常契约，没有加召回、补位或无界兜底。

Local 在经租户、索引、软删、当前版与上传者过滤的全部有效块上算 Dense 与 BM25。Milvus 先选 `max(top_k*4,20)` 个 Dense 候选，再经 SQL 可见性收窄后算 BM25，词频/文档频率与候选宇宙不同；本修复不令两库等价。窗口之外的纯关键词可遗漏；上传者或版本过滤也可能耗尽 Dense 窗口，少返回或空返回不表示 Local 没有合格文档。独立稀疏召回须另 ADR，资源 ACL 仍 Planned，默认 local 与 Milvus Partial 保持。

## 文件与证据

- `app/rag/vectorstore/milvus.py`：仅全零分支是本卡业务修复。文件中同期显式目标写入/清理来自主 Agent 的 RAG-032 整改，不能归本卡。
- `tests/test_rag_036_hybrid_semantics.py`：三种全零分数守护、关键词窗口/过滤差异和预算累计/缺失usage/超预占守护。
- `scripts/rag_hybrid_semantics_check.py`：固定2维单位向量，真实 Milvus 7切片，保留全新collection、SQLite、数据集与代码指纹。
- `scripts/rag_hybrid_embedding_check.py`：已授权合成语料，text-embedding-v3 1024维，3个真实模型切片；SQLite事务预占HTTP次数/tokens/费用，禁隐式重试。保留usage、请求标识与向量供零费用复验。
- `evals/reports/rag-036-zero-red-20261008.txt`：修前3条精确分数断言失败，实际0.032786885而期望0.016393443，退出1。最初fixture曾触发索引激活probe身份异常，那次未算有效红测；测试直接登记合成active索引来隔离重建流程。
- `evals/reports/rag-036-zero-green-20261008.txt`：修后3 passed，退出0。
- `evals/reports/rag-036-regression-20261008.txt`：本卡、RAG基础、向量库policy、RRF实验回归共58 passed，退出0。
- `evals/reports/rag-036-final-tests-20261008.txt`：验证器加固后17 passed，包含布尔/负值/字符串/数组/超预占usage拒绝、未知费用为null、模型/维度/语料/报告/向量/数据集篡改拒绝。
- `evals/reports/rag-036-fixed-real-milvus-20261008.json`：真实 Milvus 2.5.11、FLAT/COSINE、固定向量7切片 pass。关键词样本在Local进入结果、Milvus只命中Dense窗内样本；过滤耗尽样本Local命中allowed、Milvus为空。
- `evals/reports/rag-036-real-embedding-20261008.json`：真实模型3切片 pass，两库排序/RRF一致、余弦误差小于1e-5。仅1次HTTP尝试，预占300 tokens、usage44 tokens；预占费用上界0.00015元，按usage价格估算0.000022元，实际账单未取得。预算总上限20次/100,000 tokens/0.10元，失败预占不退款。价格依据 [阿里云官方页面](https://help.aliyun.com/zh/model-studio/embedding)，2026-10-08复核0.0005元/千tokens。
- `evals/reports/rag-036-fixed-real-milvus-final-20261008.json` 与 `evals/reports/rag-036-real-embedding-verified-reuse-20261008.json`：补充代码/数据集指纹与零费用复验，实际退出0。保存向量附 `vectors.metadata.json`，绑定原成功收费报告hash、合成语料hash、模型/维度/deployment、数据集版本与向量hash；复用拒绝篡改。首次收费报告在验证器加固后补记未变的合成语料指纹和provenance_note，HTTP次数/usage未改。

上述固定与真实小切片分别证明检索语义和真实模型/服务链路；不证明总体检索质量、生产性能或线上数据重建正确性。没有调用holdout，没有改冻结基线，未删除既有远端collection或重要数据。

## 可复现命令

指定解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。pytest 每次短UUID目录 `data/t/<uuid12>`，独立 `DATABASE_URL=sqlite:///./data/t/<uuid12>.db`、`PYTHONUTF8=1`。

```powershell
python -m pytest tests/test_rag_036_hybrid_semantics.py tests/test_rag.py tests/test_vectorstore_policy.py tests/eval/test_rrf_k_experiment.py -q --basetemp data/t/<new-uuid12>
python scripts/rag_hybrid_semantics_check.py --apply --report evals/reports/<new-report>.json
python scripts/rag_hybrid_embedding_check.py --apply --report evals/reports/<new-report>.json --vectors <saved-vectors.json>
python -m ruff check --no-cache scripts/rag_hybrid_semantics_check.py scripts/rag_hybrid_embedding_check.py tests/test_rag_036_hybrid_semantics.py app/rag/vectorstore/milvus.py
```

命令中的python须替换为指定绝对解释器。真实Embedding脚本不带 `--vectors` 会计费，所有复验使用报告中的vectors路径，保留同一累计budget SQLite。首次ruff因沙箱缓存写入拒绝失败，沙箱外无缓存检查定位并修正两条新增脚本lint后，实际检查退出0。生产部署、整个RAG门禁与最终业务验收由主 Agent 汇总。

向量库类型检查 `python -m mypy app/rag/vectorstore/ --no-incremental --cache-dir evals/artifacts/rag036-mypy-cache` 沙箱外实跑退出0，5个文件无问题，输出 `evals/reports/rag-036-mypy-20261008.txt`。首次沙箱内执行因 `.mypy_cache/missing_stubs` 写权限失败，不当成类型错误。此项不是整个app类型门禁通过。

## 回滚与未做项

回滚仅恢复Milvus全零分支，无schema迁移。直接登记active索引的隔离测试不验索引切换生命周期。测试collection与SQLite保持用于核验，不自动清理。

## 关闭复核（2026-10-08 终轮）

- **前置闭合**：RAG-032（Embedding 索引身份与切换治理）已于本轮关闭为 done；原 blocked_by 中「RAG-032 新 collection 重建与旧索引保护缺口」不再存在。
- **RAG-015 依赖判定**：RAG-015 是写侧闭环（默认 local、可切换 Milvus、写入向量归一化），其验收第 5 条明确「记录跨库检索差异，质量对照由 RAG-036 承接」，即 RAG-036 是该条目的下游承接方而非被阻塞方。本卡四条验收（全零不造排名、纯关键词遗漏报告、真实 Milvus 切片可复现、新增召回架构另 ADR）均不依赖 RAG-015 的写侧功能，真实 Milvus 2.5.11 切片与 text-embedding-v3 真实模型链路已独立证明读侧语义。RAG-015 继续按其自身排期推进。
- **独立验证复跑**（指定解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`）：
  - `pytest tests/test_rag_036_hybrid_semantics.py -v` → **17 passed，退出 0**。
  - 隔离 `DATABASE_URL=sqlite:///./data/t/<uuid>.db` + `PYTHONUTF8=1` 下回归 `tests/test_rag.py tests/test_vectorstore_policy.py tests/eval/test_rrf_k_experiment.py` → **52 passed，退出 0**。不隔离数据库时 3 个 upload 用例会命中本机持久化开发库 10-06 旧记录而假失败，与本卡无关。
  - `ruff check`（milvus.py + 两脚本 + 测试）→ **All checks passed，退出 0**。
  - `mypy app/rag/vectorstore/ --no-incremental` → **Success: no issues in 5 files，退出 0**。
- **独立审查**：`docs/reviews/2026-10-08-rag-036-hybrid-semantics-review.md`，6 通过 / 2 警告 / 0 问题，同意关闭。本卡业务改动经 git diff 确认为一行（`sparse_order = list(range(len(ordered)))` → `[]`）；红测修前 doc0=2/61、修后 1/61。
- **非阻塞警告**（不构成本卡关闭条件，留作后续打磨）：
  - W1：Milvus 全零分支缺少 Local 已有的 `bm25_all_zero reason=...` 结构化日志，生产可观测性略弱。
  - W2：7 切片固定向量记录的 milvus.py 指纹与当前代码有小漂移，差异落在 RAG-032 区、融合行未变；最终 `real-embedding-verified-reuse` 报告指纹已与当前代码一致。
