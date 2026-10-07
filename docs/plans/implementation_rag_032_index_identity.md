# RAG-032 实现计划：Embedding 索引身份与受控切换

日期：2026-10-07
分支：`rag-batch-20261007`，基线 HEAD `58da8bf`
契约出处：`tasks.yaml` RAG-032；`docs/adr/0008-embedding-index-identity-and-switching.md`（Accepted，2026-10-06）
风险：L2

---

## 1. 目标与现状差距

| 契约要求 | 现状 | 差距 | 证据 |
|---|---|---|---|
| 绑定 provider / model / dim / index_version | 只有 `EmbeddingProvider.model` 与 `.dim` 两个裸类属性，无统一身份概念 | **缺** | `app/rag/embeddings/base.py:68-70` |
| 写入与查询均核对索引身份 | 写入侧**零校验**；查询侧按「本次查询向量长度」跳过异维分块，同维异模型长度相同 → 直接混用 | **缺** | `app/rag/service.py:423`；`app/rag/vectorstore/local.py:170,183-185` |
| 新模型新索引，校验后切换 | `RAG_VECTOR_STORE` 只是字符串 switch，工厂零校验 | **缺** | `app/rag/vectorstore/factory.py:13-19` |
| 旧版持续可读 / 可回退 | 没有任何索引版本概念，回退无从谈起 | **缺** | 全仓无 `index_version` 代码实体 |
| health 展示身份 | `/api/health` 只暴露 `{status, backend}`，无 model/dim | **缺** | `app/api/routes/health.py:91` |

补充事实（影响实现边界）：

- **Local 后端没有独立索引容器**，向量直接存 `rag_document_chunks.embedding`（`app/models/rag.py:136`）。
  Local 上的「新索引」只能靠行级归属列 + 检索过滤实现，不能靠换表名。
- **`MilvusVectorStore.add()` 在 `app/` 下从未被调用**（调用点只有 `service.py:1035` 与
  `retention.py:89` 的 delete）。Milvus 写入链路实际是断的，本卡**不补这条写路径**
  （属 RAG-015 范围），只保证身份校验在 Milvus 侧同样生效。
- Alembic 单 head，当前 HEAD = `a1b2c3d4e5f6`。

---

## 2. 非目标（本卡不做）

- 不原地改模型、不默认切 Milvus（契约 non_goals）。
- **不重建真实索引**（批次授权边界），也不执行 schema 到生产库的迁移。
- 不补 Milvus 写入链路（RAG-015）。
- 不改检索公式、不改 RRF、不冻结基线（RAG-036 / RAG-005）。
- 不自动把历史数据认定与当前配置兼容（ADR-0008:22 明确禁止）。

---

## 3. 设计

### 3.1 身份抽象（新增 `app/rag/index_identity.py`）

```python
@dataclass(frozen=True)
class EmbeddingIndexIdentity:
    backend: str          # local | milvus
    provider: str         # EMBEDDING_PROVIDER 配置标签（不是实现类名）
    model: str            # text-embedding-v3
    deployment: str       # 由 base_url 派生，去凭据；无则留空，不猜
    dim: int
    index_version: str    # 显式版本，默认 "1"
    normalization: str    # 只允许 l2（当前实现的真实约定）
    metric: str           # 只允许 cosine（当前实现的真实约定）

    def key(self) -> str: ...          # 稳定标识，不含密钥/凭据
    def collection_name(self) -> str:  # Milvus 集合名；Local 不用
```

> **实现结果修正（第二轮）**：`EmbeddingProvider` 最终**没有**新增 `index_identity()`。
> 身份的唯一生产点是 `app/rag/index_identity.py::identity_from_provider(provider)`，
> 运行时与脚本共用它。原因是 `provider` 若取 `type(provider).__name__`，OpenAI
> 官方 / Ollama / 各类兼容服务都会落成同一个 `OpenAICompatibleEmbeddingProvider`，
> 两个不同端点、相同 model/dim 会得到**完全相同**的身份键；改用配置标签
> `EMBEDDING_PROVIDER` + 由 `base_url` 派生的 `deployment` 才能区分真实向量空间。

### 3.2 存储（新表 + 迁移）

新表 `rag_embedding_indexes`：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | str PK | 索引 ID |
| `name` | str unique | 集合名 / 逻辑索引名 |
| `backend` | str | local / milvus |
| `provider` / `model` / `deployment` | str | 身份来源 |
| `dim` | int | |
| `index_version` | str | |
| `normalization` / `metric` | str | |
| `status` | str | `preparing` / `active` / `retired` / `failed` |
| `identity_key` | str | `key()` 结果，用于快速比对 |
| `created_at` / `activated_at` / `retired_at` | datetime nullable | 切换可追踪 |
| `notes` | str nullable | 重建/回退记录 |

`rag_document_chunks` 加 `index_id`（nullable，外键逻辑关联，不改现有列语义）。
历史行 `index_id` 为 NULL = **未知身份**。

迁移写法沿用 `a1b2c3d4e5f6_add_document_chunk_plan.py:21-33` 的幂等 `add_column`。

### 3.3 校验点（本卡核心）

| 位置 | 现状 | 改为 |
|---|---|---|
| 写入 `service.py:423`（摄取）与 `:1060`（重解析） | 无校验 | 写 `embedding` 时同时落当前 active 索引的 `index_id`；若目标索引 `status != active` → 抛错 |
| 查询 `local.py:170,183` | 按**查询向量长度**跳过 | 改为按 **active 索引的 `index_id`** 过滤；维度校验以索引登记的 `dim` 为准，不以查询向量为准 |
| 查询 `milvus.py` | 集合名固定 `MILVUS_COLLECTION`，检索不过滤身份 | **实现结果修正**：集合名改为取生效索引的 `name`；`_verify_schema` 仍只校验字段存在性（新增 `index_id`）；身份校验发生在 `_resolve_index()`（无 active 或身份不符 → 抛错），检索表达式与 SQL 回查都按 `index_id` 过滤 |
| 工厂 `factory.py:13` | 字符串 switch | **未按计划改动**：身份校验落在 `MilvusVectorStore._resolve_index()` 与各 backend 调用点，工厂只做后端选择。把校验放在工厂会让直接构造 store 的路径绕过 |

**历史数据处理（ADR-0008:22 要求的「登记路径」）**：
`index_id IS NULL` 的 chunk **默认不参与检索**（拒绝混用，而不是静默 skip）。
提供显式登记入口（脚本 `--adopt`），要求：人工确认当前 provider/model/dim 与历史数据一致，
且抽样校验历史向量的维度与登记 `dim` 相符，才把 NULL 批量写为当前索引 ID。
**不自动认定兼容。**

### 3.4 切换与回退（新增 `scripts/rebuild_embedding_index.py`）

```
prepare  --to-provider/--to-model/--to-dim/--to-version
   → 建 index 记录（status=preparing，新 name）
   → 全量重建：把所有 is_current 文档重新切分+嵌入，写入新 index_id
   → 校验：数量一致、身份一致、抽样检索命中
   → 失败：status=failed，旧索引仍 active，旧读路径不受影响
activate --index-id
   → status: preparing → active；旧索引 → retired（**不删**，保留回退）
rollback --index-id
   → 把指定 retired 索引重新 active
   → 硬性要求：回退必须同时给出对应模型配置，禁止只改 collection 名而仍用新模型查旧向量
      （ADR-0008:31）——脚本在 activate/rollback 前校验 settings 的 provider/model/dim
      与目标索引身份一致，不一致直接拒绝
```

**本批次内不执行真实重建**，只在 SQLite 临时库 / fixture 数据上跑通并留下可复现证据。

### 3.5 health 展示

`/api/health` 的 `checks.vector_store` 增加：
`provider` / `model` / `dim` / `index_version` / `normalization` / `metric` / `index_status`。
同步 `app/schemas/admin.py:56-60` 的 `VectorProbeStatus` 与 `admin_system.py:25-45`。

**展示不等于校验**——ADR-0008:11 已明确，health 只用于可观测，不替代 §3.3 的校验点。

---

## 4. 验收对账表（对照原始契约）

| 原始 acceptance | 可观察期望 | 证据方式 |
|---|---|---|
| 同维度异模型阻断混用 | 用 model A 建索引并写入，切到同 dim 的 model B 后写入/检索均拒绝 | 新测试 + 变异 |
| 新 collection 全量重建与回滚证据 | 脚本跑 prepare→校验→activate→rollback，旧索引仍可读 | SQLite 临时库实跑输出 |
| 旧版持续可读 | 切到新索引后，retired 的旧索引记录仍在且可 re-activate | 新测试 |
| （ADR）未知旧身份拒绝混用 | `index_id IS NULL` 的 chunk 不进检索结果 | 新测试 |
| （ADR）切换失败保留旧读路径 | prepare 阶段注入失败 → 旧索引仍 active、检索正常 | 新测试 |

---

## 5. 文件清单（遵守 allowed_paths）

| 文件 | 动作 |
|---|---|
| `app/rag/index_identity.py` | 新增 |
| `app/rag/embeddings/base.py` | 加 `index_identity()` 默认实现 |
| `app/models/rag.py` | 新增 `EmbeddingIndex`；`DocumentChunk` 加 `index_id` |
| `alembic/versions/<new>.py` | 新迁移（down_revision `a1b2c3d4e5f6`） |
| `app/rag/service.py` | 两处写入落 `index_id` + 校验 |
| `app/rag/vectorstore/local.py` | 按 `index_id` 过滤 + 按登记 dim 校验 |
| `app/rag/vectorstore/milvus.py` | `_verify_schema` 校验身份 |
| `app/rag/vectorstore/factory.py` | 取 store 时校验 active 索引 |
| `app/api/routes/health.py` / `app/api/routes/admin_system.py` / `app/schemas/admin.py` | 展示身份 |
| `app/core/config.py` / `.env.example` / `README.md` | 新增 `EMBEDDING_INDEX_VERSION` 等配置 |
| `scripts/rebuild_embedding_index.py` | 新增切换脚本 |
| `tests/test_rag_032_index_identity.py` | 新增 |
| `docs/rag/索引身份与切换.md` | 新增实现说明 |

---

## 6. 风险与回滚

- **L2**：改动覆盖写入与查询主链路。回滚 = 回退本卡提交；`index_id` 列 nullable，
  旧代码读到 NULL 行为不变。
- **污染风险**：批量写 `index_id` 属数据迁移，**本卡不在生产/共享库执行**，
  只在临时库与测试库验证。
- **行为变化**：历史 chunk（NULL）默认不进检索 —— 这是**有意的收紧**（ADR-0008 要求），
  但会让「升级前已有知识库」在登记前查不到东西。必须在实现说明与 README 写明登记步骤。

---

## 7. 实现结果对账（2026-10-07 第二轮，独立审查后修正）

首轮提交 `671901f` 经独立审查（`docs/reviews/2026-10-07-RAG-032索引身份与切换独立审查.md`）
判为「不建议关闭」（High 6 / Medium 5 / Low 1），本轮按审查结论修正，并补 §5.4 的最低补测。

### 7.1 与 §5 文件清单的差异（以实际提交为准）

| 计划文件 | 实际情况 |
|---|---|
| `app/rag/embeddings/base.py` 加 `index_identity()` | **未做**。身份统一由 `identity_from_provider()` 生产，理由见 §3.1 修正 |
| `app/rag/vectorstore/factory.py` 校验 active 索引 | **未做**，校验点改在 store 与 backend，理由见 §3.3 |
| `app/api/routes/admin_system.py` / `app/schemas/admin.py` 展示身份 | **未做**。管理页不在 RAG-032 的 `allowed_paths` 内，本卡只改已授权的 `app/api/routes/health.py`（并已在 `tasks.yaml` 显式扩展边界并记录原因） |
| `app/rag/vectorstore/milvus.py` 只改 `_verify_schema` | 实际改了集合解析、`_resolve_index`、检索表达式、SQL 回查与父块复核 |

### 7.2 审查项关闭情况

| 编号 | 处置 |
|---|---|
| H-01 查询侧同维异模型混用 | 已修：`VectorStore.hybrid_search` 增加 `identity` 参数，三个 backend 传入，Local/Milvus 共用 `resolve_read_index_for()` |
| H-02 无法写 preparing 目标 / 旧分块被删 | 已修：`RAGService(write_index_id=...)`；写目标 ≠ active 时不删旧分块；父块复核限定同一 `index_id` |
| H-03 激活/回退可绕过 | 已修：校验下沉到 `activate_index`（failed / 空 / 身份全字段比对），脚本只传完整 identity |
| H-04 Milvus 未接入身份治理 | 已修：集合名取生效索引 `name`、schema 要求 `index_id`、检索与回查按 `index_id` 过滤，并以假 pymilvus 覆盖 |
| H-05 无 active 时读历史块 | 已修：改为 `IndexUnavailableError`；空库才走 no_hit |
| H-06 provider 取实现类名 | 已修：配置标签 + `base_url` 派生 deployment，运行时与脚本共用同一函数 |
| M-01 adopt 无证据 | 已修：`--evidence` / `--declared-model`，证据落 `notes`，同维异模型拒绝 |
| M-02 normalization/metric 不生效 | 已修：只接受 `l2` / `cosine`，其它值构造即报错；health 报告实际行为 |
| M-03 可观察性不足 | 已修：health 的向量库检查项在 unknown / legacy 隔离 / 不支持配置时为 `degraded` 并带 `reason`；Local 过滤后为空且存在被隔离数据时记 `warning`；README 补升级影响与操作步骤 |
| M-04 测试固化了错误行为 | 已修：修订为严格隔离断言，专项用例 18 → 55 条 |
| M-05 越过 allowed_paths | 已修：`tasks.yaml` 显式加入 `app/api/routes/health.py` 并在 `execution.progress` 记录原因 |
| L-01 文档与字段漂移 | 已修：条数更正、`fingerprint` / `identity_key` 分列、实现说明与计划同步 |

### 7.3 本轮仍未关闭（需后续卡承接）

- **Milvus 真实写入闭环**：`MilvusVectorStore.add()` 在 `app/` 下仍无调用点，
  真实 Milvus 连通性验证仍属 RAG-015。
- **检索结果缓存绑定索引身份**：仓库尚无检索缓存，ADR-0008:25 由 RAG-019 承接，
  届时缓存键必须包含 active index identity。
- **激活前的抽样检索 / 权限 / 完成度校验**：本轮只做了「实际分块数 > 0」与完整身份
  比对；抽样检索命中校验与人工授权门仍缺，**真实重要数据重建仍需另获授权**。
