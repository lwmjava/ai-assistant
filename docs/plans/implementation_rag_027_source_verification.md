# RAG-027 授权引用原文只读核验 — 实现说明

> 任务卡：RAG-027（P1，L2，前置 RAG-026）
> 权限依据：[ADR-0007 引用原文只读核验权限](../adr/0007-rag-source-verification-permission.md)（Accepted）
> 关联：ADR-0001 §10（uploader 检索隔离）、ADR-0003（时效过滤开关）
> 日期：2026-10-07

## 1. 做了什么

新增一个**只读**的块级核验入口，让「检索时能命中」的同租户成员可以回查被引用块的原文与定位信息，
同时不扩大任何控制面权限（删改 / 重解析 / 发布 / 整文件下载 / 资源 ACL）。

| 层 | 改动 |
| --- | --- |
| 受控 Service | 新增 `app/rag/evidence.py`（唯一核验实现，每次请求重新鉴权） |
| HTTP 入口 | 新增 `GET /api/rag/chunks/{chunk_id}/evidence`（`app/api/routes/rag.py`） |
| 前端 | 新增 `frontend/src/components/knowledge/ChunkEvidence.tsx`；在检索命中块与文档分块处挂「核验原文」入口 |
| 前端接口层 | `frontend/src/api/rag.ts` 新增 `useChunkEvidence`；`frontend/src/types/api.ts` 新增 `ChunkEvidenceOut` / `ChunkLocatorOut` |
| 测试 | 新增 `tests/test_rag_027_source_verification.py`（21 条） |

未改动：`can_read_document` / `can_control_document` / `can_write_document`、控制面详情与整文件下载端点
（`GET /api/rag/documents/{id}`、`GET /api/rag/documents/{id}/download`）的判断逻辑与权限。

## 2. ADR-0007 逐条映射

| ADR-0007 条款 | 落点 | 测试 |
| --- | --- | --- |
| §1 tenant 下可核验同租户、未软删、检索状态可见的有权命中块 + 标题 / 版本 / 页码 / 段落 / 源范围 | `load_chunk_evidence` 复用 `can_read_document`（同租户 + 未软删 + 当前版 + uploader 归属），再叠加时效语义；`_locator()` 从 `chunk_metadata` 取 `page` / `section_path` / `source_start` / `source_end`，没有的一律 `None` | `test_tenant_mode_peer_verifies_authorized_hit`、`test_tenant_mode_returns_locator_metadata`、`test_tenant_mode_missing_metadata_is_explicit_null` |
| §2 不提供整章 / 整文件旁路读取；父块须独立复核同文档关系 + 同一检索授权 + 返回范围 | `_authorized_parent()`：父子必须 `parent.document_id == chunk.document_id`，父块所属文档再走一遍 `_document_is_verifiable`；默认只给定位信息，`include_parent_content=true` 才带父正文（仍是单个块） | `test_parent_defaults_to_locator_without_content`、`test_parent_content_only_when_authorized`、`test_parent_in_other_tenant_document_is_denied`、`test_parent_owned_by_other_uploader_is_denied`、`test_parent_of_replaced_version_is_denied` |
| §3 uploader 下仅本人上传内容；管理员核验读路径不豁免；管理权限按 ADR-0001 控制面矩阵 | 复用 `can_read_document`：uploader 模式下 `TENANT_ADMIN` 也落到 `doc.user_id == user.id`；控制面仍走 `can_control_document` / `can_write_document` | `test_uploader_mode_peer_cannot_verify_owners_chunk`、`test_uploader_mode_tenant_admin_is_not_exempt`、`test_control_plane_matrix_unchanged_for_admin` |
| §4 每次核验重新从受控 Service 检查主体 / 租户 / Document-Chunk 关联 / 当前版本与生效规则 / 软删；来源标识与缓存文本不作授权证明；越权或缺失失败关闭，不返回正文或存在性细节 | 端点无缓存、无「来源票据」入参；先 `session.get(DocumentChunk)` 再按 chunk 自己记录的 `document_id` 取 `Document`，并校验 `chunk.tenant_id == doc.tenant_id`；可选 `document_id` 只是调用方声称值，不一致即拒；失败统一 `404 + EVIDENCE_DENIED_MESSAGE` | `test_claimed_document_mismatch_is_denied`、`test_chunk_document_tenant_mismatch_is_denied`、`test_cross_tenant_chunk_is_denied`、`test_denial_hides_existence_details` |
| §5 保持时效过滤开关语义；未发布 / 已替换 / 软删不通过普通核验读取；旧引用失效明确反馈，不自动指向新版 | `_document_is_verifiable()`：`RAG_EFFECTIVE_DATE_FILTER` 打开时再过 `document_is_live()`；关闭时沿用 `is_current` 语义（与检索面一致）。旧块被拒，**不会**回退到同组新版 | `test_replaced_version_is_denied`、`test_unpublished_draft_is_denied`、`test_soft_deleted_document_is_denied` |
| §6 不授予删改 / 重解析 / 发布 / 整文件下载 / 资源 ACL | 新增端点只读；不改控制面端点与判定 | `test_verifier_cannot_delete_or_reparse_owners_document`、`test_verifier_cannot_download_source_file` |

## 3. 返回字段契约

`GET /api/rag/chunks/{chunk_id}/evidence?document_id=&include_parent_content=`

```jsonc
{
  "chunk_id": "…",            // 命中块
  "document_id": "…",
  "document_title": "共享手册",
  "version_state": "published",
  "chunk_index": 0,
  "content": "该块正文",       // 只含这一个块
  "source": "rag027-shared",
  "page": 3,                   // 没有则 null
  "section": "第二章 / 2.1 计费", // section_path 字符串数组会拼成 " / " 连接
  "source_start": 40,          // 源范围，没有则 null
  "source_end": 96,
  "parent": {                  // 无父块或父块未通过独立鉴权时为 null
    "chunk_id": "…",
    "chunk_index": 0,
    "page": null,
    "section": null,
    "source_start": null,
    "source_end": null,
    "content": null            // 默认 null；include_parent_content=true 且父块独立鉴权通过才非空
  }
}
```

**返回上限**：1 个命中块正文 +（可选）1 个父块正文。响应里没有整文档正文、源文件路径、内容哈希、ACL
或任何控制面字段——字段集合被测试逐个钉住（`_EVIDENCE_FIELDS`）。

## 4. 鉴权顺序（每次请求，无缓存）

1. 主体权限：`require_permission("knowledge_bases", "read")`（不足 403，不进入核验逻辑）。
2. 取块：`session.get(DocumentChunk, chunk_id)`，不存在 → 拒绝。
3. 取文档：按 **块自己记录的** `document_id` 取 `Document`，不存在 → 拒绝。
4. 关联自洽：`chunk.tenant_id == doc.tenant_id`，否则视为数据被拼改 → 拒绝。
5. 声称关联：传入了 `document_id` 且 `chunk.document_id != document_id` → 拒绝。
6. 检索面授权：`can_read_document(doc, user)`（同租户、未软删、`is_current`、uploader 归属）。
7. 时效：`RAG_EFFECTIVE_DATE_FILTER` 打开时再过 `document_is_live()`。
8. 父块：独立复核同文档关系 + 同一检索授权 + 限定返回范围。

任一步不满足：统一 `404` + 固定文案 `引用原文不可用或无权核验`，**不返回正文，也不回显块 ID 或
「存在但无权」**。异常原因只留在服务端。

## 5. 已知取舍

1. **system_admin 沿用检索面豁免。** ADR-0001 §10 / RAG-026 的 `read_scope_for()` 在 uploader 模式下
   对 `SYSTEM_ADMIN` 返回「不过滤上传者」，即系统管理员在检索面能看到全部当前版。核验面必须等于检索面，
   否则「检索命中却不能核验」会逼调用方转向控制面详情接口——那正是 ADR-0007 想避免的旁路。
   `TENANT_ADMIN` 在 uploader 模式下**不豁免**（ADR-0007 §3 明确要求），已由测试锁定。
2. **父块默认只给定位信息。** 要父正文必须显式 `include_parent_content=true`，且仍只返回单个父块，
   不做整章拼接；父块未通过独立鉴权时整体返回 `null`（连父块 ID 都不给），以免泄漏存在性。
3. **父块必须同文档。** 父子跨文档（他人文档、他租户文档、旧版本文档）一律不认 — 这比「逐项判父文档权限」
   更保守，也让「旧版本父块」这类场景天然被拒。
4. **不缓存、不签发票据。** 每次核验都打库；性能代价换来「权限变化立即生效」，符合 ADR-0007 §4。
5. **未做的事**：不提供批量核验、不提供按文档导出全部块正文、不提供源文件下载（这些属于整文件读取，
   ADR-0007 明确不授予）。历史会话引用失效时前端显示后端文案「引用原文不可用或无权核验」，不猜测块或版本。

## 6. 验证

```
DATABASE_URL="sqlite:///…/data/tmp-rag027-<n>.db" python -m pytest tests/test_rag_027_source_verification.py -q --basetemp=data/pytest-tmp/rag027-<n>
```
21 条全绿；`tests/ -k "rag or chunk or context or embedding"` 无新增失败；`cd frontend && npx tsc -b --force` 通过。

### 变异验证（每处均被抓红后还原）

| # | 变异 | 变红测试 | 条数 |
| --- | --- | --- | --- |
| 1 | `can_read_document` 去掉同租户校验 | `test_cross_tenant_chunk_is_denied`、`test_denial_hides_existence_details` | 2 |
| 2 | `can_read_document` 去掉 `is_current` 校验 | `test_replaced_version_is_denied`、`test_unpublished_draft_is_denied` | 2 |
| 3 | `can_read_document` 去掉 uploader 限定 | `test_uploader_mode_peer_cannot_verify_owners_chunk`、`test_uploader_mode_tenant_admin_is_not_exempt` | 2 |
| 4 | `_authorized_parent` 不做独立鉴权（沿用子块授权） | `test_parent_in_other_tenant_document_is_denied`、`test_parent_owned_by_other_uploader_is_denied`、`test_parent_of_replaced_version_is_denied` | 3 |
| 5 | 失败时区分 403/404 并回显块 ID | 9 条（软删、旧版、草稿、跨租户、存在性、uploader×2、错误关联、租户自洽） | 9 |
| 6 | `can_read_document` 去掉软删校验 | `test_soft_deleted_document_is_denied` | 1 |
| 7 | 信任调用方传入的 `document_id` | `test_claimed_document_mismatch_is_denied` | 1 |
| 8 | 不校验 `chunk.tenant_id == doc.tenant_id` | `test_chunk_document_tenant_mismatch_is_denied` | 1 |

还原方式：`git checkout -- app/rag/access.py`（其余文件用改动前副本覆盖），
`grep -rn "MUTATION" app/` 为空 + 21 条全绿确认无残留。

## 7. 回滚

关闭 `GET /api/rag/chunks/{chunk_id}/evidence` 与前端 `ChunkEvidence` 入口即可；不改数据、不改控制面权限，
回退后旧接口权限不扩大、原文不删除。
