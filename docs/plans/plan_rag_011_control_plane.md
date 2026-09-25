# RAG-011：控制面权限、审计与单一当前版

> 状态：已实现（RAG-011 `done`）
> 分支：`fix/rag-005-006-review`
> 来源：交付排期 A1；评审修复方案阶段 2 / L2
> 日期：2026-09-25
> 截止：2026-10-07

检索面与控制面分开。成员只操作自己的当前版；租户管理员管本租户含历史版；系统管理员可跨租户删除/重解析并审计。重解析不改 `is_current`；同一版本组只有一个当前版。删除仍是物理删除。

## 目标

1. 修订 [`docs/adr/0001-knowledge-base-permission.md`](../adr/0001-knowledge-base-permission.md) 矩阵。
2. 实现 [`app/rag/access.py`](../../app/rag/access.py)、导入/重解析、路由与审计。
3. 知识库列表区分当前版与历史版（管理员可见历史）。

## 非目标

不放宽对话检索；不做软删除（RAG-012）；不做发布接口与完整状态机（RAG-013）。

## 验收

```powershell
pytest tests/test_rag_access.py tests/test_rag_permission_semantics.py -v
ruff check app/rag/access.py app/rag/import_jobs.py app/api/routes/rag.py
mypy app/rag/access.py app/rag/import_jobs.py
```

## 风险与回滚

L2。回滚权限与重解析语义及 ADR-0001。

## 实现方案

先写失败测试，再改权限、重解析和列表。对话检索继续只查本租户 `is_current=true`，不改 `local.py` / `milvus.py` 的检索条件。

### 1. 两条读路径

`can_read_document` 继续服务检索侧的「同租户当前版」。控制面另增判定，不复用它：

| 操作 | 成员 | 租户管理员 | 系统管理员 |
|---|---|---|---|
| 列表 / 详情 | 仅自己上传且 `is_current=true` | 本租户全部版本 | 任意租户全部版本 |
| 删除 / 重解析 | 同上 | 本租户全部版本 | 任意租户全部版本 |
| 对话检索 | 本租户当前版（含他人上传） | 同左 | 同左，不因跨租户放宽 |

`RAG_KB_SCOPE=uploader` 只保留为成员当前版列表的回滚开关；管理员控制面始终能看到历史版。跨租户只对系统管理员的控制面放开。

现有 `test_kb01_peer_lists_same_tenant_current_document` 与 `test_same_tenant_list_detail_and_search_share_current_documents` 把「成员列表可见他人当前版」锁成期望。改为：成员列表/详情看不到他人文档；同租户检索仍能命中他人当前版。`test_stale_version_not_in_list_or_detail` 对成员保持拒绝；补租户管理员可见历史版。

### 2. 权限函数与列表

改 `app/rag/access.py`：

- `can_control_document(doc, user)`：成员要求 `same_tenant`、`user_id` 相同且 `is_current`；租户管理员要求 `same_tenant`（含历史版）；系统管理员对任意文档为真。
- `can_write_document` 改为调用 `can_control_document`。
- 列表查询放在 `RAGService.list_documents`：成员加 `user_id` 与 `is_current`；租户管理员只按 `tenant_id`，去掉 `is_current` 过滤；系统管理员不加租户条件。

`GET /documents/{id}` 用 `can_control_document`，拒绝时 404。

`DELETE` 现有路由在 `can_write_document` 之前用 `doc.tenant_id != current_user.tenant_id` 直接 404，会挡住系统管理员。删掉这道租户短路，只留权限函数。向量删除使用文档所属 `tenant_id` 构造 `RAGService`，不用操作者租户。

`DocumentOut` 增加 `is_current: bool`。`frontend/src/pages/Knowledge.tsx` 的 `DocumentRow` 用徽章标「当前版」或「历史版」。成员接口不会返回历史行。

### 3. 审计

只给系统管理员的跨租户删除和重解析补审计，复用 `audit_event`。不新建表。

- 删除：沿用 `AuditAction.KNOWLEDGE_BASE_DELETE`。`details` 只含 `tenant_id`、`document_id`、`action=delete`。操作者用 `user`。
- 重解析：在 `POST /documents/{id}/reparse` 受理成功后写 `AuditAction.KNOWLEDGE_BASE_REINDEX`。`details` 只含 `tenant_id`、`document_id`、`action=reparse`。不写标题、正文、路径。
- 同租户删除保持现有审计调用。后台导入执行器里不写审计（那里没有 `Request`）。

### 4. 重解析不误发布

`create_reparse_job` 的 `tenant_id` 改为文档的 `tenant_id`，不能写成操作者租户。

`import_jobs` 里 `reparse_document_id` 分支：

- 内容哈希不变：维持现在的去重返回。
- 内容变化：就地替换该文档的分块与向量，保留该行的 `is_current`、`version_group_id`、`version_number`。不插入新 `Document`，不把同组其他行的 `is_current` 改掉。
- 普通上传（没有 `reparse_document_id`）仍可新建当前版并取消同组旧当前版。那不是重解析。

### 5. 单一当前版

新增写入辅助：每当某行要变成 `is_current=true`，同一事务内把同 `version_group_id` 的其他行设为 `false`，再提交。重解析就地更新不调用它。

另加 Alembic 部分唯一索引：`rag_documents(version_group_id) WHERE is_current = 1`（SQLite 与 PostgreSQL 都支持）。这是数据约束，不只在接口里判断。实施时把 `alembic/` 补进 `tasks.yaml` 的 RAG-011 `allowed_paths`。已有双当前版数据不在本任务迁移清理；索引创建前若库内已有重复，迁移应失败并停住，不静默删行。

### 6. ADR-0001

修订矩阵，使控制面与检索面分行。写明本修订只覆盖知识库列表、详情、删除、重解析。不改用户、审计产品和其他资源的跨租户规则。资源级 ACL 仍为 Planned。

### 7. 实施顺序

1. 改权限测试为上表期望（先失败）。
2. 实现 `can_control_document`、列表/详情/删除，以及跨租户删除审计。
3. 重解析就地更新与跨租户重解析审计；补「重解析历史版后原当前版仍唯一」用例。
4. 单一当前版辅助函数与 Alembic 索引。
5. `DocumentOut.is_current` 与知识库页徽章。
6. 修订 ADR-0001。

### 8. 验证

```powershell
pytest tests/test_rag_access.py tests/test_rag_permission_semantics.py tests/test_rag_import_jobs.py -v
ruff check app/rag/access.py app/rag/import_jobs.py app/rag/service.py app/api/routes/rag.py
mypy app/rag/access.py app/rag/import_jobs.py app/rag/service.py
cd frontend; npm run typecheck
```

未验证：真实 LLM、Milvus 闭环、生产库上的唯一索引迁移、知识库页浏览器点击。前端以类型检查为证，浏览器走查留到实现该页时补做。
