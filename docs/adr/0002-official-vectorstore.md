# ADR-0002 正式 VectorStore

> 状态：**Accepted**  
> 任务：`RAG-003`  
> 批准日期：2026-09-13  
> 批准人：技术负责人（本仓库个人开发者）  
> 风险：L2  
> 本阶段不切换默认 `RAG_VECTOR_STORE`（已是 `local`）。

## 1. 背景

默认 Local，同时存在 Milvus 适配。产品和历史设计曾把 Milvus 写成生产主路径，缺少闭环证据。

## 2. 事实

| 声明 | 证据 |
|---|---|
| 默认 `RAG_VECTOR_STORE=local` | `app/core/config.py` |
| Local 摄取、混合检索、按文档删除可用 | `app/rag/vectorstore/local.py`；`tests/test_rag.py` |
| Milvus 适配存在，缺真实集群闭环 | `app/rag/vectorstore/milvus.py` |
| Evaluation 需要可离线复现 | `evals/README.md`；`RAG-004` / `RAG-005` |

## 3. 决定

选择 **选项 A**：

- **正式后端**：Local（SQLite + NumPy）。默认配置、文档、pytest 门禁、Evaluation 基线都用它。
- **实验后端**：Milvus。适配保留，状态为 `Partial`，不得宣称生产可用。
- **评测**：固定 Local。Mock Embedding 只能证明链路，不能证明生产检索质量。

未选 B（现在把 Milvus 定为生产目标）和 C（删除 Milvus 适配）。

## 4. 能力支持矩阵

| 能力 | Local（正式） | Milvus（实验） |
|---|---|---|
| 默认配置 | Implemented | 否 |
| 摄取写入 | Implemented | Partial |
| 混合检索 | Implemented | Partial |
| 重解析删除旧向量 | Partial（有主链，持续补） | 未证明 |
| 文档删除清理向量 | Implemented | Partial |
| 评测基线 | 应用 | 不应用 |
| 生产对外宣传 | 可作为默认开发/基线后端 | 禁止 |

## 5. 退出实验条件（另开 ADR 才能升格 Milvus）

1. 真实或可复现测试 Milvus 上摄取后可检索。
2. 重解析后旧向量不可被检索。
3. 删除后向量清理且计数可核对。
4. 租户过滤与 Local 及 ADR-0001 读语义一致。
5. 有可重复命令、失败报告、备份和回滚说明。

## 6. 性能、运维、兼容、回滚

| 项 | Local | Milvus（实验） |
|---|---|---|
| 性能预期 | 单机/开发规模；不承诺分布式 SLA | 未承诺 |
| 运维 | 随应用 SQLite；备份即数据库文件 | 需独立集群与凭据，本阶段不作为发布依赖 |
| 兼容 | 保持默认；不迁出现有 Local 索引 | 开启 `RAG_VECTOR_STORE=milvus` 才使用，失败不得改默认 |
| 回滚 | 保持 `local` 与旧索引 | 改回 `local`，不删除 Local 数据 |

本阶段不迁索引、不改默认、不要求安装 `pymilvus`。

### 生效日期开关与全量载入（RAG-010，只记录，不改代码）

`RAG_EFFECTIVE_DATE_FILTER` 默认 `false`。关闭时 Local 的 SQL 用 `is_current` 收窄候选。打开后不再加该条件，按租户全量载入分块，再在 Python 里用 `visible_chunks_with_status` 逐文档过滤。

检索面和控制面不能共用一个 `is_current`。对话检索只命中本租户已发布的当前版本。管理列表要能按状态看到历史版、预告版和已替换版。在控制面能按状态查询、检索面仍只查已发布当前版之前，不得为了生效日期把检索改成全量载入。生产打开该 Flag 前须另立性能项。本记录不改 `local.py`，不下推日期谓词，不改 Milvus，也不实现版本状态机。

## 7. 集成测试计划

正式门禁（Local，现有即可继续补）：

| 编号 | 场景 | 命令/位置 |
|---|---|---|
| VS-01 | 摄取后可检索 | `tests/test_rag.py` |
| VS-02 | 删除后向量清理 | `tests/test_rag.py`；`tests/test_p0_regression.py` |
| VS-03 | 跨租户不命中 | `tests/test_rag_permission_semantics.py` |
| VS-04 | 默认后端为 local | `tests/test_vectorstore_policy.py` |

实验（未配置 Milvus 则 skip，不作为发布门槛）：

| 编号 | 场景 |
|---|---|
| VS-M1 | 摄取后可检索 |
| VS-M2 | 重解析后旧向量失效 |
| VS-M3 | 删除后向量清理 |

## 8. 回滚

保持 `RAG_VECTOR_STORE=local` 和现有 Local 索引。不删除 Milvus 代码，直到另有 Accepted ADR。
