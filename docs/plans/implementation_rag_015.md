# RAG-015 实现说明：默认保持 local、可切换 Milvus、写入向量规范化

日期：2026-10-08
任务卡：`tasks.yaml` RAG-015
风险等级：L2

## 功能完成了什么

1. **写入侧 L2 归一化固化**：在 `app/rag/vectorstore/base.py` 新增模块级函数
   `l2_normalize(vector)`，使用纯 Python `math.sqrt` 实现（不引入 numpy 到 base 层）。
   `app/rag/service.py` 的两处写入路径（正常摄取 `_persist_chunks` 和 rebuild/reparse）
   在 `json.dumps(vector)` 之前统一调用 `l2_normalize`，确保入库向量范数=1。
2. **写入侧维度校验**：两处写入点在归一化前先校验 `len(vector) == self._write_index().dim`，
   不匹配抛 `IndexIdentityError`，拒绝把异维向量写进索引。
3. **Milvus `validate_add` 防御性归一化校验**：在 `app/rag/vectorstore/milvus.py` 的
   `validate_add()` 中，解析向量后检查 `np.linalg.norm(vector) ≈ 1.0`（容差 1e-5），
   不满足抛 `IndexIdentityError("写入向量未 L2 归一化")`。正常路径 service.py 已归一化；
   此校验防止调用方绕过 service 直接写入未归一化向量。
4. **Milvus 写侧链路已接通**：`RAGService.ingest_text` 在提交前调用
   `await self._vector_store.add(persisted_rows)`（既有代码，本次补了守护测试
   `test_ingest_calls_vector_store_add`）。Milvus 后端的 `add()` 调用 `validate_add()`
   后执行 `collection.upsert(entities)`。
5. **相似度非占位**：Milvus `hybrid_search` 的 `similarity` 取自 `h.distance`
   （COSINE 度量下即余弦相似度），不存在硬编码 1.0 占位。已有守护测试
   `test_milvus_similarity_returns_real_cosine_not_placeholder`。

## 默认 local 路径

- `RAG_VECTOR_STORE=local`（默认值，未改）。摄取时 `LocalVectorStore.add()` 是空操作
  （分块已随主库 `DocumentChunk` 表持久化），检索时 `local.py` 从主库读出向量。
- `local.py` 检索侧现算归一化（lines 232-239）保留不动，作为防御性兜底。
- 默认 local 下启动与写入正常：`tests/test_rag_015_milvus_switch.py` 24 passed。

## 切换到 Milvus 的路径

1. 设置环境变量 `RAG_VECTOR_STORE=milvus`，配置 `MILVUS_URI`。
2. 首次使用时通过 rebuild/activate 登记 Milvus 后端的 embedding 索引
   （见 ADR-0008）。
3. 摄取路径会自动调用 `MilvusVectorStore.add()` → `validate_add()` →
   `collection.upsert()`。
4. 写入向量已归一化，Milvus COSINE 度量下点积=余弦成立。

## 切回 local 的路径

1. 把 `RAG_VECTOR_STORE` 改回 `local`（或不设置，默认即 local）。
2. local 后端的向量数据一直存在主库 `DocumentChunk.embedding` 字段中——
   切 Milvus 期间摄取的分块同样写入了主库（Milvus 的 `add()` 是在主库事务内
   追加向量同步，不删除主库行）。因此切回 local 后，之前摄取的文档在 local
   检索路径下仍然可见，不静默丢数据。
3. 注意：切回 local 后，Milvus 集合中的残留向量不会被自动清理；如果后续
   再次切到 Milvus，旧集合中的残留向量会被 `index_id` 过滤拦截
   （见 `milvus.py` hybrid_search 中的 `where col(DocumentChunk.index_id) == target_index.id`）。

## 代码位置

| 文件 | 修改 |
|---|---|
| `app/rag/vectorstore/base.py` | 新增 `l2_normalize()` 函数；import `math` |
| `app/rag/service.py` | 两处写入点归一化 + 维度校验；import `l2_normalize` |
| `app/rag/vectorstore/milvus.py` | `validate_add()` 增加归一化校验 |
| `tests/test_rag_015_milvus_switch.py` | 新增 3 个测试（l2_normalize 工具、摄取后 DB 向量已归一化、维度不匹配拒绝） |

## 验证命令与结果

```powershell
# 测试（conda env: ai-assistant）
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_rag_015_milvus_switch.py -v
# 结果：24 passed（1 个 PermissionError 是 Windows tmp 目录清理问题，非测试失败）

D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_rag.py tests/test_chunking.py -q
# 结果：52 passed, 11 errors（全部为 Windows tmp rm_rf 权限问题，非测试失败）

# ruff
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m ruff check app/rag/vectorstore/base.py app/rag/vectorstore/milvus.py app/rag/service.py tests/test_rag_015_milvus_switch.py
# 退出码 0

# mypy
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m mypy app/rag/vectorstore/base.py app/rag/vectorstore/milvus.py app/rag/service.py
# Success: no issues found in 3 source files
```

## 明确没做的事

- 没有把默认 `RAG_VECTOR_STORE` 切成 Milvus（仍为 local）。
- 没有改检索公式（local.py 检索侧现算归一化保留不动）。
- 没有用真实 Milvus 实例验证端到端写入命中（需要真实 Milvus 服务；
  本次验证通过注入假 store/假 collection 的单元测试覆盖链路）。
- 没有把 2026-09-29 的五条脚本当作切换证据。
- 没有推送、提交或部署。
- 跨库检索差异（local vs Milvus 的召回差异）由 RAG-036 承接，本卡不做质量对照。

## 残余风险

- 真实 Milvus 端到端写入命中未验证（无真实 Milvus 服务）；代码路径已通过
  假对象守护，真实环境首次切 Milvus 时仍需手动确认。
- 历史已存在的未归一化向量（本次改动之前入库的）不会被自动重写；
  下次 reparse/rebuild 时会自动归一化。
