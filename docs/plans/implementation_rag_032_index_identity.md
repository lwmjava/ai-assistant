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

### 7.3 第二轮复审（2026-10-07）后的整改 —— 见 §7.4

第二轮复审报告（`docs/reviews/2026-10-07-RAG-032索引身份与切换复审-第二轮.md`）
判「仍不建议关闭」：H-01～H-05 与 M-01/M-02/M-03/M-05 确认真修，但新增
**H-07 功能缺陷** 与 M-06～M-09。§7.2 里的「H-06 已修」在本轮被降级为「部分修」，
根因与修法写在 §7.4。

### 7.4 H-07 的根因、裁定与落地（本批）

#### 7.4.1 根因：两套身份来源 + 一句自相矛盾的 docstring

`scripts/rebuild_embedding_index.py` 此前有两个取身份的函数：

| 函数 | 身份来源 | `deployment` |
|---|---|---|
| `_identity(model, dim, version)`（脚本自造，`prepare` / `rebuild` 用） | 手工拼 `EmbeddingIndexIdentity(...)` | **写死 `""`** |
| `_current_runtime_identity()`（`activate` / `rollback` / `adopt` 用） | `identity_from_provider(get_embedding_provider())` | 由 `base_url` 派生 |

默认配置 `EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`
非空，于是运行时身份带着 `https://dashscope.aliyuncs.com/compatible-mode/v1`，
脚本登记出来的是空串——两者**永不相等**。

- `prepare` 自己 rc=0（登记了个错身份的行，成了库里的垃圾），`activate` 必然 rc=3；
- `rebuild --model X --dim N --apply` 更糟：`RAGService(write_index_id=...)`
  用运行时身份核对目标索引，`deployment` 不符 → 每篇文档都抛 `IndexIdentityError`
  → `rebuilt=0`、索引标 `failed`。运维看到的是「重建全部失败」，而不是「目标身份写错」；
- **该函数的 docstring 当时正写着**「与运行时共用 `identity_from_provider` 的字段来源
  （provider 标签、deployment、normalization、metric）」——注释与实现直接矛盾，
  这也是它没被第一轮的 H-06 整改发现的原因。

#### 7.4.2 裁定：`--model` / `--dim` / `--version` 改为**断言**，保留「覆盖语义」会造成真实的静默错误

团队裁定：以 `identity_from_provider(get_embedding_provider())` 为**基底**，覆盖参数改为
对真实 provider 的断言（不一致就打印期望值与实际值并 rc=3）。未采用「保留覆盖语义、
只把 deployment/provider/normalization/metric 取自运行时」的备选，理由是：

- 脚本拿不到 provider 之外的任何信息，凭命令行补出来的字段只能是假的；
- 备选方案在 `rebuild --model X --dim N --apply` 上会**写出错误数据**：
  嵌入仍由当前 provider（旧模型）产生，却登记到标着 X 的索引名下，
  激活时身份校验还会通过（因为激活时配置也已切成 X）——这是**静默的跨平台混用**，
  比 H-07 原本「失败得很响」的缺陷更危险。

复核过是否存在「依赖覆盖语义的文档化换模型流程」：README 与脚本文档里的步骤
`prepare → rebuild --apply → activate` 中，`rebuild` 用当前 provider 做嵌入，
因此整个流程本来就要求**先改配置**；改为断言与该流程一致，只是把「先改配置」
从隐含要求变成显式报错。

#### 7.4.3 本批实际改动

| 文件 | 改动 |
|---|---|
| `scripts/rebuild_embedding_index.py` | `_identity()` 以运行时身份为基底 + 参数断言；`cmd_rebuild` 不再有「两套来源」分支；`prepare --json` 改为 `_describe_index()`（原先取 `EmbeddingIndex.describe()`，该方法只存在于 `EmbeddingIndexIdentity` 上，`hasattr` 兜底让它恒输出 `{}`）；模块 docstring 的用法改写、「硬约束」加一条目标身份来源约束，删掉不再可用的 `prepare --model X` 示例 |
| `app/rag/index_registry.py` | ADR-0008:23 的第 4 项**检索校验**：`activate_index()` 先落状态、再抽样检索、失败则 `_revert_switch()` 原样撤销后抛 `IndexIdentityError`；新增 `probe_retrieval_hits()`（用它自己分块的向量做自匹配，不调用嵌入接口）、`identity_of_row()`、`_apply_switch()` / `_revert_switch()` |
| `app/rag/index_identity.py` | `__post_init__` 把 `normalization` / `metric` 归一化后再入库（此前只按小写比较却原样存进 `key()`，登记的 `L2` 会与运行时的 `l2` 永久不匹配，且 health 的小写判定会把它误报成 supported） |
| `tests/test_rag_032_rebuild_cli.py`（新增） | 6 个子命令各 ≥1 条 CLI 级用例；含全链路 `prepare → rebuild --apply → activate → rollback` |
| `tests/test_rag_032_index_identity.py` | 新增 M-08（`search()` → `_read_index_id()` 真实链路收窄）、M-09 正例 + 2 个反例、L-04 归一化用例；重写 L-03 的凭据用例（换带 `base_url` 的真实 provider 桩，使 `if identity.deployment:` 分支真正执行）；给 `test_all_backends_carry_query_identity` 加「Local 必须把 identity 传进 `resolve_read_index_for`」的断言 |

#### 7.4.4 CLI 实证（整改前后同一套探针脚本，均不连外部服务）

整改前（`fbf5730` 干净工作树）：

```text
$ prepare --model text-embedding-v3 --dim 1024
  rc=0   已登记新索引 local__v1__164f09ccfc9f（status=preparing）
         identity_key = local|openai|text-embedding-v3||1024|1|l2|cosine   ← deployment 为空
$ activate --index-id <id>
  rc=3   索引身份错误：当前配置身份 local|openai|text-embedding-v3|https://dashscope.aliyuncs.com/compatible-mode/v1|1024|1|l2|cosine
                      与目标索引 local__v1__164f09ccfc9f 的身份 local|openai|text-embedding-v3||1024|1|l2|cosine 不一致。
```

整改后（HEAD + 本批改动的干净工作树）：

```text
$ prepare --model text-embedding-v3 --dim 1024 --json
  rc=0   identity_key = local|openai|text-embedding-v3|https://dashscope.aliyuncs.com/compatible-mode/v1|1024|1|l2|cosine
         {"id": "63edbcb4…", "identity_key": "local|openai|…", "fingerprint": "19144ea9057c", "identity": {…}}   ← --json 不再恒为 {}
$ activate --index-id <id>
  rc=0   已激活 local__v1__19144ea9057c；同后端其它 active 索引已转 retired（保留未删）。
```

#### 7.4.5 变异清单（9 处，每处均有对应用例变红，改后立即按字节还原并校验 md5）

| # | 变异点 | 变红用例数 |
|---|---|---|
| H-07 | 脚本 `_identity()` 恢复自造身份（`deployment=""`） | 8 |
| M-06 | 关掉 `--model/--dim/--version` 断言（`if False:`） | 1 |
| M-07 | Local 不再把 `identity` 传进 `resolve_read_index_for` | 3（含 `[native]` / `[langchain]`） |
| M-08 | `_read_index_id()` 恒返回 `None` | 1（新增的那条，修订前官方用例全绿） |
| M-09 | 去掉激活前的抽样检索校验 | 3（含 1 条 CLI） |
| 新增 | 检索校验失败后不调用 `_revert_switch` | 2 |
| L-03 | `deployment_from_base_url` 不剥离 userinfo | 2（含原先装饰性的那条） |
| L-04 | `normalization` / `metric` 不再归一化入库 | 1 |
| L-05 | `prepare --json` 恒输出 `{}` | 6 |

变异全部在干净 git worktree 中执行（不在主工作树动手，避免与其它卡并发改动互相覆盖），
每次都用「备份 md5 → 变异 → 跑测试 → 还原 → 比对 md5」的方式确认**零残留**。

### 7.5 本轮仍未关闭（需后续卡承接）

> 此节保留第三轮复审时点（2026-10-07）的历史缺口，不表示2026-10-08的最终代码。
> 当前收尾与远端重建整改见 [2026-10-08实施说明](implementation_rag_032_remote_rebuild_fix_20261008.md)：
> L-02/N-01已修；初次Milvus摄取写入及相似度已由RAG-015修复；重建现已显式写preparing目标、
> 保护旧active、记录补偿并有全新合成真实collection生命周期证据。llamaindex运行时仍未验证，
> 缓存仍由RAG-019承接；最终独立复核与状态更新由主Agent完成，不能根据本历史节或单测数量关闭。

- **Milvus 真实写入闭环**：`MilvusVectorStore.add()` 在 `app/` 下仍无调用点，
  真实 Milvus 连通性验证仍属 RAG-015。
- **Milvus `index_id` 的写入分支**：本卡只覆盖了检索侧（检索表达式 + SQL 回查）的
  `index_id` 过滤，`add()` 里 `index_id` 的**写入**既没有真实调用点也没有桩级用例。
  **移交 RAG-015 时必须显式验收这一条。**
- **检索结果缓存绑定索引身份**：仓库尚无检索缓存，ADR-0008:25 由 RAG-019 承接，
  届时缓存键必须包含 active index identity。
- **人工授权门**：保留为运维流程（脚本是显式命令，不自动切换；生产重要数据重建与
  切换仍需另获授权）。
- **llamaindex backend 的身份传递只有静态依据**：本机缺 `llama_index`，
  `test_all_backends_carry_query_identity[llamaindex]` 显式 skip，未获运行时证明。
- **L-02 配置注释与 `.env.example` 未对齐**：`app/core/config.py:120-121` 仍声明
  `l2 | none` / `cosine | ip | l2`，而 `EmbeddingIndexIdentity.__post_init__` 对非
  l2 / 非 cosine 构造即报错。二者当前分别被 RAG-029（config.py）与并发批次
  （`.env.example`）占用，**本批不做**，待它们落地后单独处理。
- Milvus 命中 `similarity=1.0` 占位 → RAG-015；管理页展示身份 → 超出 `allowed_paths`。
