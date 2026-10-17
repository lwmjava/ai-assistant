# RAG-026 上传者范围贯穿检索授权 — 实施计划

批次：`docs/plans/plan_rag_batch_20261007.md`；基线提交 `92da115`；分支 `rag-batch-20261007`。
契约出处：`tasks.yaml` RAG-026；已批准决定 ADR-0001 §10（uploader 检索隔离方向）。

## 1. 目标

`RAG_KB_SCOPE=uploader` 时，uploader 判定必须贯穿**对话检索**链路（当前只带 `tenant_id` 的入口），
在检索取候选之前完成过滤，而不是先召回再丢弃。

验收（原文）：

- uploader A/B 同租户互不可见
- tenant 共享保留
- 跨租户拒绝

交付（原文）：鉴权主体/有效读范围在检索前过滤，覆盖适配/父块/摘要。

## 2. 现状（开工实测）

| 位置 | 事实 |
|---|---|
| `app/rag/access.py:32 can_read_document` | 已实现 uploader 判定（soft-delete / 跨租户 / 非当前版 / uploader 模式收窄） |
| `app/rag/access.py` 调用面 | 只被 `app/rag/service.py:775 _can_access`（控制面）与测试使用；**检索面完全没用** |
| `app/rag/service.py:644 RAGService.search` | 只把 `self.tenant_id` 交给 `backend.retrieve`；无 user |
| `app/rag/backend/base.py:29 retrieve` | 签名 `(query, *, tenant_id, top_k)` |
| `app/rag/vectorstore/local.py:138 hybrid_search` | SQL 只过滤 `DocumentChunk.tenant_id` + `Document.deleted_at is None` + `is_current`；**无 user 过滤** |
| `app/rag/vectorstore/milvus.py:187` | 向量侧只有 `tenant_id` 表达式，候选回 SQL 后再过滤 deleted/is_current；**无 user 过滤** |
| `app/rag/service.py:677 _expand_parent_chunks.load_rows` | 父块回查只过滤租户/未删/当前版；**无 user 过滤** |
| `app/services/chat_service.py:183 _build_retriever` | `RAGService(session, user.tenant_id)` —— 典型「只携 tenant_id」入口 |
| `app/api/routes/rag.py:932 /search` | 同样只传 `current_user.tenant_id` |

**结论**：缺陷可复现——uploader 模式下同租户 A/B 的文档在对话检索中互相可见。

## 3. 方案

### 3.1 新增有效读范围值对象（app/rag/access.py）

```python
@dataclass(frozen=True)
class ReadScope:
    tenant_id: str
    uploader_id: str | None = None   # None = 同租户全部（tenant 模式，或系统管理员）
```

- `read_scope_for(user) -> ReadScope | None`：`user is None` 返回 None（保持现有「无主体」入口行为不变）；
  uploader 模式且非系统管理员 → `uploader_id=user.id`；否则 None。
- 语义与 `can_read_document` 完全一致（删/旧版/跨租户由既有 SQL 过滤承担，不重复判定）。

### 3.2 贯穿链路（全部为向后兼容的可选参数）

`RAGService(reader=...)` → `make_retriever()` / `search()` → `RagBackend.retrieve(read_scope=...)`
→ `VectorStore.hybrid_search(read_scope=...)` → SQL `Document.user_id == uploader_id`

- 三套后端（native / langchain / llamaindex）全部透传。
- 两个向量库实现都加过滤：local 在主 SQL 上过滤；milvus 在候选回查 SQL 上过滤（向量侧无 user 字段，
  在返回前过滤，不晚于结果成形；此差异写进实现说明，不由本卡新增 Milvus 字段）。
- 父块回查 `_expand_parent_chunks.load_rows` 同步加 `Document.user_id` 过滤；
  父块与子块必须同文档，已由 `parent_links` 约束，权限仍按文档归属判定。

### 3.3 对话入口

`app/services/chat_service.py._build_retriever` 传入 `user`，使对话检索带上有效读范围。

## 4. 非目标

- 不新增资源级 ACL（ADR-0001 仍 Planned）。
- 不改变默认 `RAG_KB_SCOPE=tenant`。
- 不改控制面权限矩阵，不改 `can_control_document`。
- 不修改 `app/api/routes/rag.py` —— **该目录不在 RAG-026 的 `allowed_paths`**。
  `/api/rag/search` 因此仍是租户级检索；此缺口单列到第 7 节上报。

## 5. 文件

| 文件 | 改动 |
|---|---|
| `app/rag/access.py` | 新增 `ReadScope` / `read_scope_for` |
| `app/rag/vectorstore/base.py` | `hybrid_search` 增加可选 `read_scope` |
| `app/rag/vectorstore/local.py` | SQL 增加 uploader 过滤 |
| `app/rag/vectorstore/milvus.py` | 候选回查 SQL 增加 uploader 过滤 |
| `app/rag/backend/base.py` | `retrieve` 增加可选 `read_scope` |
| `app/rag/backend/native.py` | 透传 |
| `app/rag/backend/langchain_backend.py` | 适配层透传 |
| `app/rag/backend/llamaindex_backend.py` | 适配层透传 |
| `app/rag/retriever.py` | `HybridRetriever` 携带 read_scope |
| `app/rag/service.py` | `RAGService(reader=)`；`search` / `make_retriever` / `_expand_parent_chunks` 应用范围 |
| `app/services/chat_service.py` | 检索器带上当前用户 |
| `tests/` | 新增失败用例 + uploader 隔离/tenant 共享/跨租户 |

## 6. 验收与证据

失败用例先红：uploader 模式下 B 检索能命中 A 的文档。再实现转绿。

- uploader：A 只见自己、B 只见自己、管理员在本卡语义下按 `can_read_document`（非系统管理员仍收窄）
- tenant：A/B/C 同租户共享保留（现有 `test_rag_permission_semantics` 必须仍绿）
- 跨租户：拒绝
- 父块：命中子块后父块仍受同一范围约束
- 命令：批次统一验证命令 + `ruff check app/rag/ app/services/` + `mypy app/rag/`

## 7. 风险与上报

| 项 | 处理 |
|---|---|
| `/api/rag/search` 未在 allowed_paths | 不擅自改；上报为集合内缺口，需授权后另卡处理 |
| Milvus 无 user 字段 | 候选回查阶段过滤并记录差异；不新增字段（属索引 schema 变更） |
| 风险等级 L2 | 回滚：去除 `read_scope` 传参与 `reader` 参数，行为回到租户级 |
