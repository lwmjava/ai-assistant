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

### 验证结果（2026-09-29，EVD-001）

仓库根目录的默认 `pytest` 指向 base 解释器 `D:\install\anaconda3\python.exe`，加载 `tests/conftest.py` 时 `ModuleNotFoundError: No module named 'sqlmodel'`，退出码 4。下面三条是同一命令文本在项目既有 conda 环境 `ai-assistant`（`D:\install\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0；pytest 8.3.4，ruff 0.8.4，mypy 1.14.1）中的输出。`mypy` 退出码为 1 后停止，`cd frontend; npm run typecheck` 未执行。

`pytest tests/test_rag_access.py tests/test_rag_permission_semantics.py tests/test_rag_import_jobs.py -v`，退出码 0：

```text
============================= test session starts =============================
platform win32 -- Python 3.12.0, pytest-8.3.4, pluggy-1.6.0 -- D:\install\anaconda3\envs\ai-assistant\python.exe
cachedir: .pytest_cache
rootdir: E:\dzmnc\python\mnc\ai-assistant
configfile: pyproject.toml
plugins: anyio-4.15.0, asyncio-0.25.0
asyncio: mode=Mode.AUTO, asyncio_default_fixture_loop_scope=function
collecting ... collected 30 items

tests/test_rag_access.py::test_kb01_peer_lists_same_tenant_current_document PASSED [  3%]
tests/test_rag_access.py::test_kb03_member_cannot_delete_peer_admin_can PASSED [  6%]
tests/test_rag_access.py::test_stale_version_not_in_list_or_detail PASSED [ 10%]
tests/test_rag_access.py::test_cross_tenant_read_and_write_denied PASSED [ 13%]
tests/test_rag_access.py::test_purge_waits_for_retention_then_removes_and_audits PASSED [ 16%]
tests/test_rag_access.py::test_publish_historical_replaces_the_previous_current PASSED [ 20%]
tests/test_rag_access.py::test_cross_tenant_delete_requires_confirmation PASSED [ 23%]
tests/test_rag_access.py::test_member_state_filter_is_ignored PASSED     [ 26%]
tests/test_rag_access.py::test_uploader_scope_hides_peer_list PASSED     [ 30%]
tests/test_rag_permission_semantics.py::test_same_tenant_list_detail_and_search_share_current_documents PASSED [ 33%]
tests/test_rag_permission_semantics.py::test_cross_tenant_search_does_not_return_foreign_documents PASSED [ 36%]
tests/test_rag_import_jobs.py::test_upload_batch_rejects_second_file_oversize_without_saving PASSED [ 40%]
tests/test_rag_import_jobs.py::test_upload_batch_returns_first_error_when_type_precedes_oversize PASSED [ 43%]
tests/test_rag_import_jobs.py::test_batch_upload_jobs_create_documents PASSED [ 46%]
tests/test_rag_import_jobs.py::test_url_import_and_reparse_updates_in_place PASSED [ 50%]
tests/test_rag_import_jobs.py::test_retry_failed_url_job PASSED          [ 53%]
tests/test_rag_import_jobs.py::test_pdf_import_job_succeeds_with_ocr PASSED [ 56%]
tests/test_rag_import_jobs.py::test_pdf_import_job_succeeds_with_cloud_ocr PASSED [ 60%]
tests/test_rag_import_jobs.py::test_pdf_import_job_fails_when_ocr_environment_missing PASSED [ 63%]
tests/test_rag_import_jobs.py::test_pdf_import_job_fails_when_cloud_ocr_http_error PASSED [ 66%]
tests/test_rag_import_jobs.py::test_pdf_import_job_persists_trace_for_ocr_provider_failure PASSED [ 70%]
tests/test_rag_import_jobs.py::test_pptx_import_job_persists_trace_for_ocr_fallback_failure PASSED [ 73%]
tests/test_rag_import_jobs.py::test_pptx_import_job_persists_trace_for_pdf_conversion_failure PASSED [ 76%]
tests/test_rag_import_jobs.py::test_url_import_job_persists_trace_for_fetch_failure PASSED [ 80%]
tests/test_rag_import_jobs.py::test_legacy_doc_import_job_succeeds PASSED [ 83%]
tests/test_rag_import_jobs.py::test_legacy_doc_import_job_truncates_user_error_and_persists_trace PASSED [ 86%]
tests/test_rag_import_jobs.py::test_legacy_doc_import_job_persists_full_trace_for_subprocess_failure PASSED [ 90%]
tests/test_rag_import_jobs.py::test_search_ignores_superseded_versions PASSED [ 93%]
tests/test_rag_import_jobs.py::test_reparse_does_not_restore_deleted_document PASSED [ 96%]
tests/test_rag_import_jobs.py::test_reparse_historical_keeps_the_existing_current_version PASSED [100%]

============================== warnings summary ===============================
D:\install\anaconda3\envs\ai-assistant\Lib\site-packages\starlette\testclient.py:40
  D:\install\anaconda3\envs\ai-assistant\Lib\site-packages\starlette\testclient.py:40: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = typing.Callable[[], typing.ContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================= 30 passed, 1 warning in 35.90s ========================
```

`ruff check app/rag/access.py app/rag/import_jobs.py app/rag/service.py app/api/routes/rag.py`，退出码 0：

```text
All checks passed!
```

`mypy app/rag/access.py app/rag/import_jobs.py app/rag/service.py`，退出码 1：

```text
app\rag\ocr\tesseract.py:216: error: Argument "stdout" to "OcrProviderError" has incompatible type "bytes | None"; expected "str | None"  [arg-type]
app\rag\ocr\tesseract.py:217: error: Argument "stderr" to "OcrProviderError" has incompatible type "bytes | None"; expected "str | None"  [arg-type]
app\rag\document_parsers\libreoffice.py:97: error: Incompatible types in assignment (expression has type "str", variable has type "list[str]")  [assignment]
app\rag\document_parsers\libreoffice.py:101: error: Argument "command" to "LegacyOfficeConversionError" has incompatible type "list[str]"; expected "str | None"  [arg-type]
app\rag\chunking\strategies\parent_child.py:40: error: Argument 1 to "ChunkParams" has incompatible type "**dict[str, int]"; expected "str | None"  [arg-type]
app\rag\chunking\strategies\parent_child.py:40: error: Argument 1 to "ChunkParams" has incompatible type "**dict[str, int]"; expected "dict[Any, Any] | None"  [arg-type]
app\rag\document_parsers\pdf.py:26: error: Cannot assign to a type  [misc]
app\rag\document_parsers\pdf.py:26: error: Incompatible types in assignment (expression has type "None", variable has type "type[PdfReader]")  [assignment]
app\rag\vectorstore\local.py:149: error: Argument 2 to "join" of "Select" has incompatible type "bool"; expected "ColumnElement[Any] | _HasClauseElement[Any] | SQLCoreOperations[Any] | ExpressionElementRole[Any] | TypedColumnsClauseRole[Any] | Callable[[], ColumnElement[Any]] | LambdaElement | OnClauseRole | None"  [arg-type]
app\rag\vectorstore\local.py:152: error: Item "datetime" of "datetime | None" has no attribute "is_"  [union-attr]
app\rag\vectorstore\local.py:152: error: Item "None" of "datetime | None" has no attribute "is_"  [union-attr]
app\rag\vectorstore\local.py:154: error: "bool" has no attribute "is_"  [attr-defined]
app\rag\vectorstore\local.py:159: error: Argument 2 to "visible_chunks_with_status" has incompatible type "Sequence[DocumentChunk]"; expected "list[DocumentChunk]"  [arg-type]
app\rag\document_parsers\office.py:29: error: Incompatible types in assignment (expression has type "None", variable has type "Callable[[str | IO[bytes] | None], Presentation]")  [assignment]
app\rag\document_parsers\office.py:142: error: Argument 1 to "float" has incompatible type "Any | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
app\rag\document_parsers\office.py:143: error: Argument 1 to "float" has incompatible type "Any | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
app\rag\document_parsers\office.py:144: error: Argument 1 to "float" has incompatible type "Any | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
app\rag\document_parsers\office.py:145: error: Argument 1 to "float" has incompatible type "Any | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
app\rag\document_parsers\office.py:433: error: Unpacked dict entry 0 has incompatible type "dict[str, object]"; expected "SupportsKeysAndGetItem[str, str | int | float | None]"  [dict-item]
app\rag\document_parsers\office.py:449: error: Unpacked dict entry 0 has incompatible type "dict[str, object]"; expected "SupportsKeysAndGetItem[str, str | int | float | None]"  [dict-item]
app\rag\document_parsers\office.py:455: error: Incompatible return value type (got "tuple[str, dict[str, object], list[ParsedBlock]]", expected "tuple[str, dict[str, str | int | bool | None], list[ParsedBlock]]")  [return-value]
app\rag\vectorstore\milvus.py:213: error: Argument 2 to "join" of "Select" has incompatible type "bool"; expected "ColumnElement[Any] | _HasClauseElement[Any] | SQLCoreOperations[Any] | ExpressionElementRole[Any] | TypedColumnsClauseRole[Any] | Callable[[], ColumnElement[Any]] | LambdaElement | OnClauseRole | None"  [arg-type]
app\rag\vectorstore\milvus.py:216: error: Item "datetime" of "datetime | None" has no attribute "is_"  [union-attr]
app\rag\vectorstore\milvus.py:216: error: Item "None" of "datetime | None" has no attribute "is_"  [union-attr]
app\rag\vectorstore\milvus.py:218: error: "bool" has no attribute "is_"  [attr-defined]
app\rag\vectorstore\milvus.py:221: error: Argument 2 to "visible_chunks_with_status" has incompatible type "Sequence[DocumentChunk]"; expected "list[DocumentChunk]"  [arg-type]
app\core\migration.py:69: error: Incompatible return value type (got "str | None", expected "str")  [return-value]
app\audit\logger.py:151: error: Argument 1 to "where" of "DMLWhereBase" has incompatible type "bool"; expected "ColumnElement[bool] | _HasClauseElement[bool] | SQLCoreOperations[bool] | ExpressionElementRole[bool] | TypedColumnsClauseRole[bool] | Callable[[], ColumnElement[bool]] | LambdaElement"  [arg-type]
app\audit\logger.py:152: error: No overload variant of "exec" of "Session" matches argument type "Delete"  [call-overload]
app\audit\logger.py:152: note: Error code "call-overload" not covered by "type: ignore" comment
app\audit\logger.py:152: note: Possible overload variants:
app\audit\logger.py:152: note:     def [_TSelectParam: Any] exec(self, statement: Select[_TSelectParam], *, params: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = ..., execution_options: Mapping[str, Any] = ..., bind_arguments: dict[str, Any] | None = ..., _parent_execute_state: Any | None = ..., _add_event: Any | None = ...) -> TupleResult[_TSelectParam]
app\audit\logger.py:152: note:     def [_TSelectParam: Any] exec(self, statement: SelectOfScalar[_TSelectParam], *, params: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = ..., execution_options: Mapping[str, Any] = ..., bind_arguments: dict[str, Any] | None = ..., _parent_execute_state: Any | None = ..., _add_event: Any | None = ...) -> ScalarResult[_TSelectParam]
app\rag\service.py:50: error: "bool" has no attribute "is_"  [attr-defined]
app\rag\service.py:449: error: "bool" has no attribute "is_"  [attr-defined]
app\rag\service.py:454: error: Item "datetime" of "datetime | None" has no attribute "is_"  [union-attr]
app\rag\service.py:454: error: Item "None" of "datetime | None" has no attribute "is_"  [union-attr]
app\rag\service.py:457: error: "datetime" has no attribute "desc"  [attr-defined]
app\rag\import_jobs.py:375: error: "bool" has no attribute "is_"  [attr-defined]
app\rag\import_jobs.py:383: error: "datetime" has no attribute "desc"  [attr-defined]
app\rag\import_jobs.py:440: error: Incompatible types in assignment (expression has type "str | None", variable has type "str")  [assignment]
app\rag\import_jobs.py:492: error: Argument "version_group_id" to "ingest_parsed_document" of "RAGService" has incompatible type "str | int | bool | None"; expected "str | None"  [arg-type]
app\rag\import_jobs.py:493: error: Argument 1 to "int" has incompatible type "str | int | bool | None"; expected "str | Buffer | SupportsInt | SupportsIndex | SupportsTrunc"  [arg-type]
app\rag\import_jobs.py:535: error: "datetime" has no attribute "asc"  [attr-defined]
Found 40 errors in 11 files (checked 3 source files)
```

点名文件里的报错在 `app/rag/service.py` 与 `app/rag/import_jobs.py`。其余报错来自 mypy 对这 3 个源文件的导入跟随。`app/rag/access.py` 本次没有单独的 error 行。

### 再次验证（2026-09-29，类型修复之后）

解释器：conda 环境 `ai-assistant`。上午那次停在 mypy 退出码 1。类型修复之后重跑本组四条，退出码都是 0。

`pytest tests/test_rag_access.py tests/test_rag_permission_semantics.py tests/test_rag_import_jobs.py -v`，退出码 0：`30 passed, 1 warning in 34.74s`。警告仍是 Starlette `anyio.abc.BlockingPortal` 的 `DeprecationWarning`。

`ruff check app/rag/access.py app/rag/import_jobs.py app/rag/service.py app/api/routes/rag.py`，退出码 0：`All checks passed!`

`mypy app/rag/access.py app/rag/import_jobs.py app/rag/service.py`，退出码 0：`Success: no issues found in 3 source files`

`cd frontend; npm run typecheck`，退出码 0：`tsc --noEmit`，无输出。

### 验证通过（2026-09-29，EVD-001 收口）

混合检索重跑隔离与 Mock 编码检查之后，本组四条在 conda 环境 `ai-assistant` 中再跑一次。退出码都是 0。RAG-012 / RAG-013 共用组同一次也全部为 0，清点表第 1–3 行改为完成。

`pytest tests/test_rag_access.py tests/test_rag_permission_semantics.py tests/test_rag_import_jobs.py -v`，退出码 0：`30 passed, 1 warning in 37.08s`。警告仍是 Starlette `anyio.abc.BlockingPortal` 的 `DeprecationWarning`。

`ruff check app/rag/access.py app/rag/import_jobs.py app/rag/service.py app/api/routes/rag.py`，退出码 0：`All checks passed!`

`mypy app/rag/access.py app/rag/import_jobs.py app/rag/service.py`，退出码 0：`Success: no issues found in 3 source files`

`cd frontend; npm run typecheck`，退出码 0：`tsc --noEmit`，无输出。RAG-012 / RAG-013 引用的是这一次，没有重跑。

未验证：真实 LLM、Milvus 闭环、生产库上的唯一索引迁移、知识库页浏览器点击。前端以类型检查为证，浏览器走查留到实现该页时补做。
