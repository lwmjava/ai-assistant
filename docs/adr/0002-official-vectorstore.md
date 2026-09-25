# ADR-0002 正式 VectorStore

> 状态：**Accepted**（2026-09-25 修订：正式后端改为 Milvus）  
> 任务：`RAG-003`；修订对齐产品需求 §5.2 / §6  
> 批准日期：2026-09-13；修订日期：2026-09-25  
> 批准人：技术负责人（本仓库个人开发者）  
> 风险：L2  
> 默认 `RAG_VECTOR_STORE` 在第 5 节门槛通过前仍为 `local`；通过后再改为 `milvus`。

## 1. 背景

产品需求将向量化存储定为 P0：生产用 Milvus 2.4+，开发用 Milvus Lite，相似度 cosine。2026-09-13 版 ADR 曾选正式 Local、Milvus 实验，与需求冲突。2026-09-25 按需求修订决定；实现证据仍不足，不得把 Milvus 写成已 `Implemented`。

## 2. 事实（修订时仍成立）

| 声明 | 证据 |
|---|---|
| 默认 `RAG_VECTOR_STORE=local` | `app/core/config.py` |
| Local 摄取、混合检索、按文档删除可用 | `app/rag/vectorstore/local.py`；`tests/test_rag.py` |
| Milvus 适配存在，缺摄取/重解析删旧向量/删除清理闭环 | `app/rag/vectorstore/milvus.py` |
| Evaluation 需要可离线复现；rag-v0.1 已在 Local 上冻结 | `evals/README.md`；`evals/reports/rag-v0.1-baseline-20260919.json` |

## 3. 决定

- **正式后端**：Milvus。开发与试点用 Milvus Lite（本地文件）；生产目标为 Milvus 2.4+。相似度 cosine。
- **评测与回退**：Local（SQLite + NumPy）用于离线评测，以及未配置或未通过第 5 节门槛时的回退。已冻结的 rag-v0.1 基线继续用 Local，不改写 `evals/reports/rag-v0.1-baseline-20260919.json`。
- **默认配置**：保持 `RAG_VECTOR_STORE=local`，直到第 5 节五条门槛均有测试证据。门槛满足后再把默认改为 `milvus`。门槛未满足前，文档不得写「生产已使用 Milvus」。
- **状态术语**：Milvus 在闭环完成前为 `Partial`；Local 为评测/回退，不再称为正式生产后端。

本修订取代 2026-09-13 的「选项 A：正式 Local」。不再另开 ADR。

## 4. 能力支持矩阵

| 能力 | Milvus（正式目标） | Local（评测/回退） |
|---|---|---|
| 产品定位 | 正式后端 | 评测与回退 |
| 默认配置（门槛前） | 否 | Implemented（当前默认） |
| 默认配置（门槛后） | 目标改为 `milvus` | 仍可显式选用 |
| 摄取写入 | Partial | Implemented |
| 混合检索 | Partial | Implemented |
| 重解析删除旧向量 | 未证明 | Partial（有主链，持续补） |
| 文档删除清理向量 | Partial | Implemented |
| 评测基线 rag-v0.1 | 不改写已冻结报告 | 已应用 |
| 生产对外宣传 | 门槛通过前禁止宣称生产可用 | 不得再写成正式生产后端 |

## 5. 标成 Implemented 的门槛（原退出实验条件）

下列五条均有可复现证据后，方可把 Milvus 标为 `Implemented`，并将默认 `RAG_VECTOR_STORE` 改为 `milvus`：

1. 真实或可复现测试中，Milvus（含 Lite）上摄取后可检索。
2. 重解析后旧向量不可被检索。
3. 删除后向量清理且计数可核对。
4. 租户过滤与 ADR-0001 读语义一致（与 Local 对照）。
5. 有可重复命令、失败报告、备份和回滚说明。

交付排期中：B1 引入 Compose 的 Milvus Lite 与健康检查中的向量库连通性；C6 用本五条核对。五条未过，则 2026-12-25 的 80% 清点不把向量库算完成。

## 6. 性能、运维、兼容、回滚

| 项 | Milvus（正式目标） | Local（评测/回退） |
|---|---|---|
| 性能预期 | 生产目标对齐需求 P95；Lite 仅开发/试点规模 | 单机/开发规模；不承诺分布式 SLA |
| 运维 | Lite：本地文件；生产：独立集群与凭据，纳入 Compose/备份 | 随应用 SQLite；备份即数据库文件 |
| 兼容 | 门槛通过前须显式 `RAG_VECTOR_STORE=milvus` | 保持当前默认；失败时改回 `local` |
| 回滚 | 改回 `local`，不删除 Local 数据 | 保持现有 Local 索引 |

门槛通过前不强制安装 `pymilvus` 作为 pytest 门禁依赖；未配置 Milvus 的用例可 skip，但 skip 不得充当第 5 节通过证据。

### 生效日期开关与全量载入（RAG-010，只记录，不改代码）

`RAG_EFFECTIVE_DATE_FILTER` 默认 `false`。关闭时 Local 的 SQL 用 `is_current` 收窄候选。打开后不再加该条件，按租户全量载入分块，再在 Python 里用 `visible_chunks_with_status` 逐文档过滤。

检索面和控制面不能共用一个 `is_current`。对话检索只命中本租户已发布的当前版本。管理列表要能按状态看到历史版、预告版和已替换版。在控制面能按状态查询、检索面仍只查已发布当前版之前，不得为了生效日期把检索改成全量载入。生产打开该 Flag 前须另立性能项。本记录不改 `local.py`，不下推日期谓词，不改 Milvus 查询实现，也不实现版本状态机。

## 7. 集成测试计划

Local 回归（评测/回退，现有继续保留）：

| 编号 | 场景 | 命令/位置 |
|---|---|---|
| VS-01 | 摄取后可检索 | `tests/test_rag.py` |
| VS-02 | 删除后向量清理 | `tests/test_rag.py`；`tests/test_p0_regression.py` |
| VS-03 | 跨租户不命中 | `tests/test_rag_permission_semantics.py` |
| VS-04 | 默认后端为 local（门槛通过前） | `tests/test_vectorstore_policy.py` |

Milvus 闭环（对应第 5 节；未配置则 skip，skip 不算通过）：

| 编号 | 场景 |
|---|---|
| VS-M1 | 摄取后可检索 |
| VS-M2 | 重解析后旧向量失效 |
| VS-M3 | 删除后向量清理 |
| VS-M4 | 租户过滤与 ADR-0001 一致 |
| VS-M5 | 备份/回滚说明与可重复命令 |

## 8. 回滚

保持 `RAG_VECTOR_STORE=local` 和现有 Local 索引，直到第 5 节门槛通过并显式切换默认。不删除 Local 或现有 Milvus 适配代码。
