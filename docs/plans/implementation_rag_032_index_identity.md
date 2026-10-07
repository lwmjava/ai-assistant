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
    provider: str         # OpenAICompatibleEmbeddingProvider | MockEmbeddingProvider ...
    model: str            # text-embedding-v3
    deployment: str       # 部署/修订标识；不存在时为空串，不猜
    dim: int
    index_version: str    # 显式版本，默认 "1"
    normalization: str    # l2 | none —— 影响向量值，必须绑定
    metric: str           # cosine | ip | l2

    def key(self) -> str: ...          # 稳定标识，不含密钥/连接串
    def collection_name(self) -> str:  # Milvus 集合名；Local 不用
```

`EmbeddingProvider` 新增 `index_identity()` 默认实现（从 `model` / `dim` 构造，
`deployment` 留空），各 provider 可覆写。这样身份的**唯一生产点**是 provider，
避免各处拼字符串。

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
| 查询 `milvus.py:90 _verify_schema` | 只校验有没有 `document_id` 字段 | 扩展：校验集合名对应的索引身份与当前 active 一致（dim / metric / provider / model），不一致 → `MilvusUnavailableError` |
| 工厂 `factory.py:13` | 字符串 switch | 取 store 时校验目标索引已 `active` 且身份匹配；失败保留旧读路径 |

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

## 7. 待确认（如与已批准决定冲突才升级）

无。ADR-0008 已裁定方案边界、禁止项与验证项；本计划只落实现细节。
唯一需要人工执行的是「历史数据登记」——属业务判断，Agent 不自动执行。
