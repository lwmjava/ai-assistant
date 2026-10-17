# RAG-015 实施计划：默认 local、可切换 Milvus、写入向量规范化

> 创建：2026-10-08
> 任务卡：tasks.yaml `RAG-015`（P0）
> 风险级别：L2
> allowed_paths：`app/rag/`、`app/core/config.py`、`.env.example`、`docker-compose.yml`、`tests/`、`docs/`、`tasks.yaml`

## 1. 目标

- 默认 `RAG_VECTOR_STORE=local` 路径下摄取与检索正常。
- 写入侧向量统一做 L2 归一化固化，不依赖 embedding provider 自己归一化。
- 写入时校验向量维度与登记索引一致。
- Milvus 写侧链路已接通（`add()` 被调用），similarity 取自真实 COSINE distance（非 1.0 占位）。
- local↔Milvus 切换路径有文档说明，切回 local 不静默丢数据。

## 2. 现状核对

| 项 | 现状 | 证据 |
|---|---|---|
| 默认后端 | `RAG_VECTOR_STORE: str = "local"` | `app/core/config.py:188` |
| Milvus add 调用 | service.py:465 `await self._vector_store.add(persisted_rows)` 已调用 | `app/rag/service.py` |
| Milvus similarity | 取 `h.distance`（COSINE 度量下即余弦），非硬编码 1.0 | `app/rag/vectorstore/milvus.py:360,409` |
| 写入侧归一化 | **未做**：service.py:450/1176 直接 `json.dumps(vector)`，不归一化 | `app/rag/service.py` |
| 检索侧归一化 | local.py:232-239 现算归一化（防御性） | `app/rag/vectorstore/local.py` |
| Mock provider | 自己归一化 | `app/rag/embeddings/mock.py:41-45` |
| OpenAI 兼容 provider | **不归一化**，返回原始向量 | `app/rag/embeddings/openai_compatible.py:123` |
| 现有测试 | 21 passed（含 add 调用守护、similarity 非占位守护） | `tests/test_rag_015_milvus_switch.py` |
| 切换脚本 | `scripts/milvus_switch_check.py`（dry-run + counterexample 已测） | scripts/ |

## 3. 方案

### 3.1 新增归一化工具函数

在 `app/rag/vectorstore/base.py` 或新建 `app/rag/vectorstore/_normalize.py` 中加：

```python
def l2_normalize(vector: list[float]) -> list[float]:
    """对向量做 L2 归一化；零向量原样返回。"""
```

### 3.2 在 service.py 写入路径固化归一化

两处写入点都要改：
- `_persist_chunks`（约 line 450）：`embedding=json.dumps(l2_normalize(vector))`
- rebuild/reparse 路径（约 line 1176）：同上

同时在写入时校验维度：
- 取 `self._write_index().dim`
- 对每个非 None vector，校验 `len(vector) == expected_dim`，不匹配抛 `IndexIdentityError`

### 3.3 保留检索侧防御性归一化

local.py 检索时的归一化保留（兼容历史未归一化数据），不删除。

### 3.4 Milvus 侧

- `validate_add()` 已有维度校验，增加归一化校验（norm ≈ 1.0，容忍 float 误差）。
- similarity 取自 `h.distance` 的逻辑已正确，不改动。

### 3.5 文档

- 在 `docs/rag/` 或实现说明中写 local↔Milvus 切换步骤与回滚方式。
- 记录跨库检索差异（脚本 `compare_results` 已支持，无真实 Milvus 时标注未验证）。

## 4. 非目标

- 不改检索公式（RRF、BM25 不变）。
- 不把默认切成 Milvus。
- 不把 2026-09-29 五条脚本当证据。
- 不推 Git、不合并、不部署。

## 5. 验收对照

| 验收标准 | 实现方式 |
|---|---|
| 默认 local 下启动与写入正常 | 现有测试 + 新增归一化测试 |
| 切 Milvus 配置后上传可在 Milvus 中查到 | add 已接通（测试守护），真实 Milvus 未连接时标注未验证 |
| 切回 local 的路径写明且不静默丢数据 | 文档 + `assert_local_preserved` 已有守护 |
| 写入向量已归一化，检索侧点积=余弦成立 | service.py 写入时归一化 + 测试验证 |
| 真实 Milvus similarity 按批准 metric 转换，禁止 1.0 占位 | 现有测试 `test_milvus_similarity_returns_real_cosine_not_placeholder` 通过 |

## 6. 验证命令

```powershell
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_rag_015_milvus_switch.py -v
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/ -k "milvus or vector or rag" -v
ruff check app/rag/ tests/
mypy app/rag/
```
