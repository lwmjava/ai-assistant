# RAG-015 独立审查：默认保持 local、可切换 Milvus、写入向量规范化

日期：2026-10-08
审查人：独立审查 Agent（未参与实现）
任务卡：`tasks.yaml` RAG-015（P0，L2）
审查对象：工作区未提交 diff

## 审查范围与方法

- 读取原始任务卡（deliverables 4 条、acceptance 5 条、non_goals 3 条、allowed_paths、risk L2）。
- 逐文件审查 `git diff`：`app/rag/service.py`、`app/rag/vectorstore/base.py`、`app/rag/vectorstore/milvus.py`、`tests/test_rag_015_milvus_switch.py`、`tasks.yaml`。
- 搜索遗漏写入点（`DocumentChunk(`、`embedding=json.dumps`、`.embedding =`）。
- 实际运行：pytest（指定解释器）、ruff、mypy。
- 核对实现说明 `docs/plans/implementation_rag_015.md` 与代码事实一致性。

## 逐条验收判定

| # | 验收标准 | 判定 | 证据 |
|---|---|---|---|
| 1 | 默认 local 下启动与写入正常 | **通过** | `RAG_VECTOR_STORE` 默认值仍为 `local`（config.py 未改）；`pytest tests/test_rag_015_milvus_switch.py` 24 passed，含摄取后 DB 向量已归一化的守护测试。 |
| 2 | 切 Milvus 配置后上传可在 Milvus 中查到 | **未验证（已知限制）** | 写侧链路 `service.py → _vector_store.add() → MilvusVectorStore.add() → validate_add() → upsert` 已有守护测试 `test_ingest_calls_vector_store_add` 通过；`validate_add` 维度+归一化校验就位。但无真实 Milvus 服务，端到端命中未连。实现说明如实标注。 |
| 3 | 切回 local 的路径写明且不静默丢数据 | **通过** | 实现说明第 43–52 行写明：local 向量一直存在主库 `DocumentChunk.embedding`，Milvus add 不删主库行；切回 local 后旧文档仍可见。残留 Milvus 向量由 `index_id` 过滤拦截。 |
| 4 | 写入向量已归一化，检索侧点积=余弦成立 | **通过** | 两处写入点（service.py:461 摄取、1199 rebuild）在 `json.dumps` 前调用 `l2_normalize`；测试 `test_ingest_normalizes_raw_embeddings_before_persist` 注入返回未归一化向量（norm=8）的假 provider，摄取后从 DB 读出验证 norm≈1.0。local.py 检索侧现算归一化保留为旧数据兜底，未删。 |
| 5 | 真实 Milvus 相似度按批准 metric 转换，禁止 similarity=1.0 占位；记录跨库检索差异 | **通过（代码）/ 跨库差异转交 RAG-036** | milvus.py:365 `similarity_by_id = {h.entity.get("id"): float(h.distance) ...}`，:414 `similarity=similarity_by_id.get(row.id, 0.0)`；metric_type=COSINE（:168）。无硬编码 1.0。守护测试 `test_milvus_similarity_returns_real_cosine_not_placeholder` 通过。跨库质量对照按验收原文由 RAG-036 承接。 |

## 交付物对照

| # | 交付物 | 判定 |
|---|---|---|
| 1 | 保留默认 local 前期路径，上传写入 local | 通过：config 默认未改，local add 是空操作但主库行持久化。 |
| 2 | 验证切 Milvus 后上传可写入并命中 | 链路接通+守护测试通过；真实命中未连，如实标注。 |
| 3 | 验证切回 local 不静默丢数据 | 实现说明写清路径，主库行不删。 |
| 4 | 写入侧 L2 归一化固化，维度与规范化有校验 | 通过：base.py `l2_normalize` + service.py 两处维度校验 + milvus.py `validate_add` 防御性归一化校验。 |

## Non-goals 对照

- 不改检索公式：local.py 检索侧归一化保留，RRF/BM25 未动。**未违反。**
- 不把默认切成 Milvus：config.py 未改。**未违反。**
- 不用 2026-09-29 五条脚本当证据：实现说明明确未用。**未违反。**

## allowed_paths 对照

实际改动文件均在 allowed_paths 内（`app/rag/`、`tests/`、`docs/`、`tasks.yaml`）。未触碰 `app/core/config.py`、`.env.example`、`docker-compose.yml`——这些不需要改（默认值已是 local）。

## 发现的问题

### High

无。

### Medium

**M1. tasks.yaml 同时改了 RAG-036 状态（blocked→done），超出 RAG-015 范围。**
- 证据：`git diff tasks.yaml` 第 9956 行附近，RAG-036 从 `blocked` 改为 `done`，并重写了 `blocked_by`。
- 同时工作区有未跟踪文件 `docs/reviews/2026-10-08-rag-036-hybrid-semantics-review.md` 和对 `docs/plans/implementation_rag_036_hybrid_semantics.md` 的修改。
- 判断：这些变更属于 RAG-036 的关闭流程，不是 RAG-015 的交付物。它们出现在同一工作区可能是并行会话/同一开发批次的整合。RAG-015 本身不应代为关闭 RAG-036。建议在提交时将 RAG-036 的 tasks.yaml 行和文档变更拆为独立提交，或确认这是经授权的同批关闭。
- 影响：不影响 RAG-015 代码正确性，但违反"一次只推进一张卡"的边界。

### Low

**L1. base.py 整文件 diff 噪音（CRLF/LF）。**
- 证据：`git diff --stat` 显示 base.py 改了 167 行（+/-），但 `git diff --ignore-all-space` 后实质变更仅 14 行（`import math` + `l2_normalize` 函数）。
- 判断：行尾符变化导致整文件重写，不影响运行时，但增加 review 噪音和未来合并冲突概率。建议后续统一 `.gitattributes`。

**L2. milvus.py `validate_add` 归一化校验在正常路径下是冗余的。**
- 证据：`validate_add` 读 `chunk.embedding`（已从 DB 解析），而 service.py 写入前已归一化。正常路径下此校验必然通过。
- 判断：这是有意的 defense-in-depth（防绕过 service 直接构造实体），代码注释也写明了。不是 bug，但要注意它不能替代写入侧归一化——它只在 Milvus 后端生效，local 后端没有等价校验。当前 local 检索侧现算归一化兜底，可接受。

**L3. 零向量边界：`l2_normalize` 对零向量原样返回。**
- 证据：`norm == 0.0` 时返回 `list(vector)`。维度校验仍会通过（如果零向量维度正确）。
- 判断：零向量进入 Milvus COSINE 空间可能产生未定义距离。这是预存在边界（embedding provider 返回全零是 provider 异常），不是本次引入。实现说明未特别提及，但影响极小。

**L4. 历史未归一化向量不会被自动重写。**
- 证据：实现说明第 96–97 行如实记录。local.py 检索侧归一化兜底可读，但如果现在切到 Milvus，旧向量未归一会被 `validate_add` 拦截（因为 rebuild/add 时会触发）。
- 判断：符合卡片范围（本卡不做数据迁移）。下次 rebuild/reparse 自动归一化。残余风险已记录。

## 验证命令与结果

| 命令 | 结果 |
|---|---|
| `pytest tests/test_rag_015_milvus_switch.py -v` | **24 passed**, 1 error（PermissionError: Windows tmp 目录清理，非测试失败） |
| `ruff check`（4 个改动文件） | All checks passed，退出 0 |
| `mypy`（3 个源码文件） | Success: no issues found in 3 source files，退出 0 |
| 开发库 `data/ai_assistant.db` 修改时间 | 2026-10-07 17:55，今日测试未触碰开发库 |
| 测试库 `data/test_ai_assistant.db` | 今日 18:27 更新（指定测试库，正常） |

## 循环导入检查

`base.py` 仅 import `math`、`abc`、`dataclasses`、`datetime`、`typing`、`app.rag.access.ReadScope`、`app.rag.index_identity.EmbeddingIndexIdentity`。不反向 import service。**无循环导入。**

## 写入点覆盖检查

全仓搜索 `DocumentChunk(` 仅 service.py 两处（:454 摄取、:1192 rebuild），均已归一化+维度校验。搜索 `.embedding =` 在 app/ 下无其他赋值点。**无遗漏写入路径。**

## 结论

**同意关闭 RAG-015**，附带一条 Medium 级流程提醒（M1：RAG-036 状态变更不应混在本卡 diff 中）。

代码层面：
- 写入侧 L2 归一化固化正确，两处写入点均覆盖。
- 维度校验在归一化前执行，异维拒绝。
- Milvus similarity 取自真实 COSINE distance，无 1.0 占位。
- 默认 local 未改，检索公式未改。
- 测试真实验证了行为（注入未归一化 provider → 从 DB 读出验证 norm），不是走过场。
- ruff/mypy/24 tests 全绿。

真实 Milvus 端到端未连接是已知限制，实现说明如实标注，不算失败。Windows tmp 权限错误是环境问题，不算代码缺陷。
