# RAG-026 上传者范围贯穿检索授权 — 实现说明

批次：`docs/plans/plan_rag_batch_20261007.md`；分支 `rag-batch-20261007`。
计划：`docs/plans/plan_rag_026_uploader_scope_20261007.md`
独立审查：`docs/reviews/2026-10-07-RAG-026上传者范围独立审查.md`

## 1. 实际完成了什么

`RAG_KB_SCOPE=uploader` 时，鉴权主体的有效读范围现在贯穿检索全链路，并且在**取候选之前**过滤，
而不是先召回再丢弃。默认 `tenant` 模式行为不变。

### 谁可以做什么（成功 / 失败行为）

| 场景 | 行为 |
|---|---|
| tenant 模式（默认） | 同租户当前版共享，检索结果与变更前完全一致 |
| uploader 模式 · 本人 | 只命中自己上传的当前版文档 |
| uploader 模式 · 同租户他人 | 命中为空；且他人文档不占用自己的 top-k 预算 |
| uploader 模式 · TENANT_ADMIN | 同样被收窄到本人（与 `can_read_document` 语义一致） |
| uploader 模式 · SYSTEM_ADMIN | 不收窄（与 `can_read_document` 一致） |
| 跨租户 | 命中为空 |
| 软删 / 非当前版 | 命中为空（沿用既有 SQL 条件） |
| 主体租户与服务租户不一致 | 构造 `RAGService` 时抛 `ValueError`，不静默降级 |

### 覆盖的检索入口

- 对话 / Agent：`ChatService._build_retriever`（`app/services/chat_service.py:185`）
- HTTP 知识库搜索：`POST /api/rag/search`（`app/api/routes/rag.py:935`）——
  经用户 2026-10-07 授权扩范围后同卡补齐
- 三套后端：native / langchain / llamaindex 的 `retrieve` 均透传
- 两个向量库：local（候选 SQL 上过滤）、milvus（候选回查 SQL 上过滤）
- 父块展开：`_expand_parent_chunks.load_rows` 同步按上传者收窄

## 2. 代码位置

| 文件 | 位置 | 作用 |
|---|---|---|
| `app/rag/access.py` | `ReadScope`（26–58 行）、`read_scope_for` | 有效读范围值对象与推导 |
| `app/rag/service.py` | `_read_scope`（类级默认 + `__init__`）、`search`、`make_retriever`、`_expand_parent_chunks` | 承载并应用范围 |
| `app/rag/retriever.py` | `HybridRetriever.read_scope` | 透传到后端 |
| `app/rag/backend/base.py`、`native.py`、`langchain_backend.py`、`llamaindex_backend.py` | `retrieve(read_scope=...)` | 三后端透传 |
| `app/rag/vectorstore/base.py`、`local.py`、`milvus.py` | `hybrid_search(read_scope=...)` | 候选阶段 SQL 过滤 |
| `app/services/chat_service.py` | 185 行 | 对话入口传 `reader=user` |
| `app/api/routes/rag.py` | 935 行 | HTTP 检索面传 `reader=current_user` |
| `tests/test_rag_026_uploader_scope.py` | 12 用例 | 本卡验收 |

## 3. 验证命令与结果

| 命令 | 结果 |
|---|---|
| `pytest tests/ -k "rag or chunk or context or embedding" -q`（独立 DATABASE_URL + 全新 basetemp，排除下一卡 WIP 文件） | **364 passed / 2 skipped / 0 failed** |
| `pytest tests/test_rag_026_uploader_scope.py -q` | **12 passed** |
| `ruff check app/` | `All checks passed!` |
| `mypy app/rag/` | `Success: no issues found in 65 source files` |

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0）。

### 失败用例有效性（变异测试，不是只跑绿）

| 变异 | 结果 |
|---|---|
| `read_scope_for` 恒不收窄上传者 | `test_read_scope_uploader_mode_narrows_to_owner`、`test_uploader_mode_peer_cannot_retrieve_owners_document`、`test_uploader_mode_filters_before_ranking` 三条转红 |
| `/api/rag/search` 去掉 `reader=current_user` | `test_uploader_mode_http_search_hides_peer_document` 转红 |

两次变异后均已还原，工作区无 `MUTATION` 残留。

## 4. 明确没做的事

- **不新增资源级 ACL**，未引入任何 ACL 表/字段/迁移（ADR-0001 仍 Planned）。
- **未改变默认** `RAG_KB_SCOPE=tenant`。
- 「摘要」路径的检索前过滤**不适用**：当前 `app/rag/` 内没有检索侧摘要召回实现
  （审查结论 C7），本卡无对应实现可改。
- Milvus 后端 uploader 过滤**未做真实验证**：本机无 Milvus 实例。实现位置在候选回查 SQL，
  早于结果成形；向量 ANN 阶段不感知上传者，极端情况下可能出现「本主体候选不足」，
  但不会越权泄漏。上线前需在具备 Milvus 的环境补测。
- 未修改 `can_control_document` 等控制面权限矩阵。

## 5. 已知语义（写清楚避免误用）

`read_scope=None` **等同同租户全可读**，不构成 uploader 隔离。面向终端用户的入口必须显式传
`reader`；只有无终端鉴权主体的后台路径（导入任务重解析/摄取、评测脚本）才允许 `None`。
这一点已写入 `app/rag/access.py`、`app/rag/service.py`、`app/rag/retriever.py` 的注释。

## 6. 回滚

去除 `read_scope` 传参与 `reader` 参数即可回到租户级检索行为；不改变数据结构，无需迁移。
