# RAG / Milvus 增强实施计划

## 背景与现状

当前 `ai-assistant` 的 RAG 已具备：本地向量库（SQLite + numpy，稠密余弦 + BM25 + RRF 融合）、混合检索器、文档摄取与生命周期接口。但对照设计文档（Phase 2 RAG 增强），**Milvus 后端与「重排 / 父文档检索 / 查询变换 / 评估」尚未真正落地**。

关键发现（已实地核查代码）：
- `app/rag/vectorstore/milvus.py` **已存在但不是真可用**：Milvus Schema 只有 `id / tenant_id / embedding`，**缺少 `document_id` 字段**，导致 `delete_by_document` 的过滤表达式永远失败（异常被静默吞掉，返回假成功）。`count` 直接读主库而非 Milvus，与本地实现口径不一致。
- 重排目前硬编码为 RRF（`_rrf`），无可配置、无可插拔抽象。
- 检索只有「单级分块」，没有父文档（parent document）两级检索。
- 没有查询变换（query rewrite / HyDE）。
- 没有离线评估基准。

本次目标：在当前 `agent-supervisor` 分支上新建 `rag-milvus` 分支，把上述能力补齐并接入，且不改动默认路径（`RAG_VECTOR_STORE=local` 行为不变）。

## 方案概览

| 子项 | 动作 | 是否影响默认路径 | 说明 |
|---|---|---|---|
| Milvus 后端修复 | 改写 `milvus.py` | 否（仅 `RAG_VECTOR_STORE=milvus` 生效） | 补全 schema、修复删除/计数、区分「不可用」与「无结果」 |
| 重排策略 | 新增 `app/rag/rerank.py` | 否（默认仍 RRF） | RRF / 加权融合 / MMR 可切换 |
| 父文档检索 | 改 ingestion + 模型 + retriever | 否（默认关闭，需配置开启） | 子块检索、父块返回，降低跨块语义断裂 |
| 离线评估 | 新增 `tests/test_rag_eval.py` | 否 | Mock 嵌入 + 固定语料，不需要真实向量库 |
| 查询变换 | 计划外（标记为可选） | 否 | 如需要再加，默认不引入额外 LLM 调用 |

## 分步计划

### 步骤 0：建分支
- 基于当前 `agent-super,visor`（`cdcdf78`）创建扁平分支 `rag-milvus`（沙箱 git 不持久化 `feature/` 嵌套名）。

### 步骤 1：修复 MilvusVectorStore（核心）
文件：`app/rag/vectorstore/milvus.py`
1. Schema 增加 `document_id`（VARCHAR）并建索引；`_connect` 在集合已存在但 schema 不符时，给出明确告警并建议 `RAG_VECTOR_STORE=local` 或重建集合（开发期可加 `drop + create` 兼容逻辑）。
2. `add`：`upsert` 同时写入 `document_id`（从 `chunk.document_id`），保证删除可用。
3. `delete_by_document`：用 `document_id == "{id}" and tenant_id == "{tenant_id}"` 正确删除，并 `collection.flush()` 使删除生效；不再静默吞异常（抛 `MilvusUnavailableError` 或记录后返回 0）。
4. `count`：用 Milvus `query` 按 `tenant_id` 计数，与本地实现口径一致。
5. 保留「缺 pymilvus 时抛 `MilvusUnavailableError`」的懒加载，便于导入期不报错。

### 步骤 2：可插拔重排策略
新增 `app/rag/rerank.py`：
- `Reranker` 抽象：输入「稠密得分排序 + 稀疏得分 + 候选结果」，输出重排后的 `ChunkResult`。
- `RrfReranker`（迁移现有 `_rrf`，默认）。
- `WeightedReranker`：稠密余弦与 BM25 各自 min-max 归一化后线性加权，`RAG_RERANK_WEIGHT` 控权重（默认 0.5）。
- `MmrReranker`：在相关性基础上加入多样性（基于向量夹角），缓解结果同质化。
- 配置：`RAG_RERANK_STRATEGY`（rrf|weighted|mmr，默认 rrf）、`RAG_RERANK_WEIGHT`。
- 接入点：`HybridRetriever` 或 `VectorStore.hybrid_search` 末尾统一调用 reranker（工厂注入）。

### 步骤 3：父文档检索（两级分块）
- `app/rag/ingestion.py`：摄取时先按「大窗口」（如 1000 字）切父块，再在父块内按「小窗口」（如 200 字 + 重叠）切子块；子块 embedding、父块仅存文本。
- `app/models/rag.py`：`DocumentChunk` 增加 `parent_id`（可空）与 `is_parent` 标记（或 `level`）。
- `app/rag/retriever.py`：命中子块后用 `parent_id` 取父块内容返回，降低跨块语义断裂；未启用父文档时维持原行为。

### 步骤 4：离线评估基准
- 新增 `tests/test_rag_eval.py`：用确定性 Mock 嵌入 + 固定少量文档/查询，断言 `top_k` 命中正确来源、计算 Recall@k 与 MRR。仅用 `LocalVectorStore`，不依赖 Milvus server。
- 可选：`scripts/rag_eval.py` 命令行跑脚本（便于后续接自动化）。

### 步骤 5：接线、文档、提交
- `app/rag/vectorstore/factory.py`：按 `RAG_RERANK_STRATEGY` 选择 reranker。
- `app/core/config.py` + `.env.example`：补 `RAG_RERANK_STRATEGY`、`RAG_RERANK_WEIGHT`（及父文档相关开关）。
- `docs/plans/后端实现说明.md`：更新模块表与测试状态。
- 用 phase0 venv 跑 `pytest`，确保全绿（Milvus 用例仅做「导入/缺依赖回退」，与 Supervisor 一致）。
- 在 `rag-milvus` 提交，**不自动合 main**，待你审核。

## 关键决策
- **不重写整个 RAG**，只在现有抽象上增量扩展，默认路径（`local` + RRF）行为保持不变，降低风险。
- **Milvus 不做集成测试**（无 Milvus server）：以「可导入 / 缺依赖回退」为主，真实联调留待你提供环境。
- **查询变换暂不默认实现**，仅在计划中标注为可选后续；如你确认需要（query rewrite / HyDE），再补。

## 待你确认
1. 父文档检索是否一并做（默认我建议做，因为能明显提升召回质量）？
2. 重排策略是否需要「学习型重排（cross-encoder）」？该策略需额外模型依赖，默认不纳入。
3. 是否需要我顺带把 `test_workflow.py` 缺 `croniter` 的既有失败一并修掉（装 `croniter` 即可）？

确认后我切换到 Craft 模式执行；或你想调整范围也直接说。
