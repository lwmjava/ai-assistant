# 三个源文件 mypy 40 条报错的原因与改法

> 日期：2026-09-29
> 来源：`mypy app/rag/access.py app/rag/import_jobs.py app/rag/service.py`
> 环境：conda `ai-assistant`，mypy 1.14.1
> 结果：退出码 1，`Found 40 errors in 11 files (checked 3 source files)`
> 状态：已实现。说明见 `docs/plans/implementation_mypy_rag_three_files.md`。未重跑 `EVD-001`，清点表未改。

`app/rag/access.py` 没有 error 行。40 条里 11 条落在 `app/rag/service.py` 与 `app/rag/import_jobs.py`，另外 29 条是 mypy 跟随导入后写在其他模块上的。本方案只解释并准备修这 40 条。不放宽 mypy，不改 `follow_imports`，不用整文件 `type: ignore` 把退出码做成 0。

这不是 `EVD-001`。`EVD-001` 禁止改 `app/`。要让那条 mypy 退出码变成 0，需要另做本方案，做完再重跑 `EVD-001`。

## 另外 29 条

`app/rag/ocr/tesseract.py`

1. 216：`Argument "stdout" to "OcrProviderError" has incompatible type "bytes | None"; expected "str | None"` `[arg-type]`
2. 217：`Argument "stderr" to "OcrProviderError" has incompatible type "bytes | None"; expected "str | None"` `[arg-type]`

`app/rag/document_parsers/libreoffice.py`

3. 97：`Incompatible types in assignment (expression has type "str", variable has type "list[str]")` `[assignment]`
4. 101：`Argument "command" to "LegacyOfficeConversionError" has incompatible type "list[str]"; expected "str | None"` `[arg-type]`

`app/rag/chunking/strategies/parent_child.py`

5. 40：`Argument 1 to "ChunkParams" has incompatible type "**dict[str, int]"; expected "str | None"` `[arg-type]`
6. 40：`Argument 1 to "ChunkParams" has incompatible type "**dict[str, int]"; expected "dict[Any, Any] | None"` `[arg-type]`

`app/rag/document_parsers/pdf.py`

7. 26：`Cannot assign to a type` `[misc]`
8. 26：`Incompatible types in assignment (expression has type "None", variable has type "type[PdfReader]")` `[assignment]`

`app/rag/vectorstore/local.py`

9. 149：`Argument 2 to "join" of "Select" has incompatible type "bool"; expected ColumnElement[...]` `[arg-type]`
10. 152：`Item "datetime" of "datetime | None" has no attribute "is_"` `[union-attr]`
11. 152：`Item "None" of "datetime | None" has no attribute "is_"` `[union-attr]`
12. 154：`"bool" has no attribute "is_"` `[attr-defined]`
13. 159：`Argument 2 to "visible_chunks_with_status" has incompatible type "Sequence[DocumentChunk]"; expected "list[DocumentChunk]"` `[arg-type]`

`app/rag/document_parsers/office.py`

14. 29：`Incompatible types in assignment (expression has type "None", variable has type "Callable[[str | IO[bytes] | None], Presentation]")` `[assignment]`
15. 142：`Argument 1 to "float" has incompatible type "Any | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"` `[arg-type]`
16. 143：同上
17. 144：同上
18. 145：同上
19. 433：`Unpacked dict entry 0 has incompatible type "dict[str, object]"; expected "SupportsKeysAndGetItem[str, str | int | float | None]"` `[dict-item]`
20. 449：同上
21. 455：`Incompatible return value type (got "tuple[str, dict[str, object], list[ParsedBlock]]", expected "tuple[str, dict[str, str | int | bool | None], list[ParsedBlock]]")` `[return-value]`

`app/rag/vectorstore/milvus.py`

22. 213：`Argument 2 to "join" of "Select" has incompatible type "bool"; expected ColumnElement[...]` `[arg-type]`
23. 216：`Item "datetime" of "datetime | None" has no attribute "is_"` `[union-attr]`
24. 216：`Item "None" of "datetime | None" has no attribute "is_"` `[union-attr]`
25. 218：`"bool" has no attribute "is_"` `[attr-defined]`
26. 221：`Argument 2 to "visible_chunks_with_status" has incompatible type "Sequence[DocumentChunk]"; expected "list[DocumentChunk]"` `[arg-type]`

`app/core/migration.py`

27. 69：`Incompatible return value type (got "str | None", expected "str")` `[return-value]`

`app/audit/logger.py`

28. 151：`Argument 1 to "where" of "DMLWhereBase" has incompatible type "bool"; expected ColumnElement[bool]` `[arg-type]`
29. 152：`No overload variant of "exec" of "Session" matches argument type "Delete"` `[call-overload]`。现有注释是 `# type: ignore[arg-type]`，盖不住 `call-overload`。

## 三个源文件上的 11 条

`app/rag/service.py`：50、449 的 `is_current.is_`；454 的 `deleted_at.is_` 两条；457 的 `updated_at.desc`。

`app/rag/import_jobs.py`：375 的 `is_current.is_`；383 的 `updated_at.desc`；440 的 `source_ref` 赋值；492、493 的 `versioning` 下标；535 的 `created_at.asc`。

## 原因与改法

运行时这些 SQL 表达式是列对象。mypy 只看见模型上的 Python 注解：`is_current: bool`、`deleted_at: datetime | None`、`updated_at: datetime`、`created_at: datetime`、`id: str`。于是 `.is_`、`.desc`、`.asc` 报在 `bool` / `datetime` 上，`==` 和 `<` 被看成普通 `bool`，传给 `join` / `where` 也不符合。

仓库里已经有正确写法：`from sqlmodel import col`，见 `app/services/admin_users.py`、`app/api/routes/audit.py`。本方案沿用 `col()`，不引入 SQLAlchemy mypy 插件，也不改模型注解。

改这些调用，不改 `Document` / `ImportJob` / `AuditLog` 的字段类型：

| 位置 | 现在 | 改成 |
|---|---|---|
| `service.py` 50、449；`import_jobs.py` 375；`local.py` 154；`milvus.py` 218 | `Document.is_current.is_(True)` | `col(Document.is_current).is_(True)` |
| `service.py` 454；`local.py` 152；`milvus.py` 216 | `Document.deleted_at.is_(None)` | `col(Document.deleted_at).is_(None)` |
| `service.py` 457；`import_jobs.py` 383 | `Document.updated_at.desc()` | `col(Document.updated_at).desc()` |
| `import_jobs.py` 535 | `ImportJob.created_at.asc()` | `col(ImportJob.created_at).asc()` |
| `local.py` 149；`milvus.py` 213 | `Document.id == DocumentChunk.document_id` | `col(Document.id) == col(DocumentChunk.document_id)` |
| `audit/logger.py` 151 | `AuditLog.created_at < cutoff` | `col(AuditLog.created_at) < cutoff` |

对应报错：service 5 条，import_jobs 的 375、383、535，local 的 149、152 两条、154，milvus 的 213、216 两条、218，audit 151。共 17 条。

这些文件现在没有导入 `col`。只改调用、不改 import，运行时会 `NameError`。一并改 import：

| 文件 | 现在 | 改为 |
|---|---|---|
| `app/rag/service.py` | `from sqlmodel import Session, select` | `from sqlmodel import Session, col, select` |
| `app/rag/import_jobs.py` | `from sqlmodel import Session, select` | `from sqlmodel import Session, col, select` |
| `app/rag/vectorstore/local.py` | `from sqlmodel import Session, select` | `from sqlmodel import Session, col, select` |
| `app/rag/vectorstore/milvus.py` | `from sqlmodel import Session, select` | `from sqlmodel import Session, col, select` |
| `app/audit/logger.py` | `from sqlmodel import Session, delete, select` | `from sqlmodel import Session, col, delete, select` |

`local.py` 没有 `from __future__ import annotations`。方案 3 的 `Sequence[DocumentChunk]` 会在函数定义时求值，还要加 `from collections.abc import Sequence`。`milvus.py` 只调用该函数，不写这个注解，不必为 `Sequence` 再加一行。`DocumentChunk` 已在 `local.py` 导入，注解里可以继续用这个名字。

### 2. `session.exec` 不接受 `Delete`

`app/audit/logger.py` 152。SQLModel 的 `Session.exec` 重载只覆盖 `Select`。这里执行的是 `delete(AuditLog)`。第 152 行已有 `# type: ignore[arg-type]`，mypy 实际错误码是 `call-overload`，所以忽略没有生效。

改法：不保留 `type: ignore`。SQLModel 0.0.22 的 `Session.exec` 对非 `SelectOfScalar` 语句只是转调 `Session.execute` 并原样返回。`Delete` 走这条路径，两处运行时结果相同。`execute` 的注解接受一般可执行语句，返回 `Result[Any]`，mypy 能通过。

```python
stmt = delete(AuditLog).where(col(AuditLog.created_at) < cutoff)
result = session.execute(stmt)
deleted = getattr(result, "rowcount", 0) or 0
```

`Result[Any]` 的注解里没有 `rowcount`，所以继续用现有的 `getattr`，不要改成 `result.rowcount`。删掉第 152 行的 `# type: ignore[arg-type]`。`where` 使用上一节的 `col()`。

### 3. 查询结果是 `Sequence`，函数参数写成了 `list`

`local.py` 159、`milvus.py` 221。`session.exec(stmt).all()` 的类型是 `Sequence[DocumentChunk]`。`visible_chunks_with_status` 的第二个参数标成了 `list[DocumentChunk]`。

改法：把 `app/rag/vectorstore/local.py` 里该函数的 `rows` 参数改为 `Sequence[DocumentChunk]`，并按方案 1 补上 `from collections.abc import Sequence`。过滤关闭时的提前返回改成 `return list(rows), ...`，以保持返回类型 `list[DocumentChunk]`。两个调用点不用再包一层 `list()`。

### 4. 版本字典的值被收成一个大联合

`import_jobs.py` 492、493。`_dedupe_or_version_existing` 的返回类型是 `dict[str, str | int | bool | None]`。下标之后 `version_group_id` 和 `version_number` 都是这个联合，对不上 `ingest_parsed_document` 要的 `str | None` 和 `int`。

改法：在 `import_jobs.py` 增加 `from typing import TypedDict`，并定义：

- `deduplicated: bool`
- `version_group_id: str | None`
- `version_number: int`
- `previous_document_id: str | None`

返回类型改成这个 `TypedDict`。调用处直接传 `versioning["version_group_id"]` 和 `versioning["version_number"]`，去掉 `int(...)`。字典里的运行时键和值不变。

### 5. `source_ref` 在两个分支里的类型不一致

`import_jobs.py` 440。URL 分支在 `if not job.source_uri: raise` 之后，`source_ref = job.source_uri` 的类型是 `str`。文件分支 `source_ref = job.source_name` 的类型是 `str | None`。mypy 把这个变量收成 `str`，后一次赋值失败。`_dedupe_or_version_existing` 接受 `str | None`。

改法：在分支前写 `source_ref: str | None`。`source_name` 为空时仍传入 `None`，不改成 `"upload.bin"`。上一行解析文件名已经用了 `job.source_name or "upload.bin"`，那一行不动。

### 6. 超时异常里的 stdout / stderr 是 `bytes`

`tesseract.py` 216、217。`subprocess.TimeoutExpired.stdout` / `stderr` 在二进制模式下是 `bytes | None`。`OcrProviderError` 要求 `str | None`。同文件成功路径和 `libreoffice.py` 的超时路径已经用 `decode("utf-8", errors="replace")`。

改法：超时分支在传入前解码。`None` 仍传 `None`。`bytes` 用同样的 `utf-8` 与 `errors="replace"`。错误类签名不变。

### 7. `command` 先是参数列表，超时分支又赋成字符串

`libreoffice.py` 97、101。`try` 里 `command = [binary, ...]`，类型是 `list[str]`。`except TimeoutExpired` 里又写 `command = " ".join(...)"`。mypy 拒绝把 `str` 赋给 `list[str]`，于是第 101 行仍把 `list[str]` 传给要求 `str | None` 的 `command`。

改法：超时分支用新名字，例如 `command_text`，不要复用 `command`。传给 `LegacyOfficeConversionError` 的仍是拼好的字符串。

### 8. 用字典拆包构造 `ChunkParams`

`parent_child.py` 40，同一行两条。`child_merged` 是 `dict[str, int]`，`ChunkParams(**child_merged)` 的键对 mypy 是未知字符串，对不上数据类里 `str | None` 和 `dict | None` 那些字段。测试里 `child_params` 只覆盖 `chunk_size`（`tests/test_chunking.py`）。运行时未知键会在构造时抛 `TypeError`。

改法：按字段显式构造，不使用 `**dict`。先放 `chunk_size`、`chunk_overlap`，再用 `params.child_params` 里属于 `ChunkParams` 字段的键覆盖。已知字段的覆盖保持。

未知键现在会在 `ChunkParams(**...)` 时抛 `TypeError`。不改成跳过。遇到未知键抛 `ValueError`，消息里带上这些键名。这仍是遇到非法键就失败。`child_params` 目前只在 `tests/test_chunking.py` 出现，且只覆盖 `chunk_size`，现有测试不会走到这个分支。合法字段以 `ChunkParams` 的数据类字段为准：`chunk_size`、`chunk_overlap`、`window_size`、`step`、`max_tokens`、`overlap_tokens`、`similarity_threshold`、`parent_strategy`、`parent_ratio`、`child_strategy`、`child_params`。

### 9. 可选导入失败时把类名赋成 `None`

`pdf.py` 26 两条，`office.py` 29。`from pypdf import PdfReader` 成功时，这个名字的类型是类本身。`except ImportError: PdfReader = None` 是在给类型赋值。`Presentation` 同理，它的类型是 `Callable[..., Presentation]`。`fitz = None` 这次没有报错，不顺手改。

改法：先声明 `PdfReader: Any = None`，再 `from pypdf import PdfReader as _PdfReader` 并赋给这个名字。`Presentation` 同样处理。两个文件都补 `from typing import Any`。它们已有 `from __future__ import annotations`，注解本身不会在运行时求值；`Any` 仍要导入，否则 mypy 报名字未定义。调用处仍写 `PdfReader(...)` / `Presentation(...)`，空依赖时仍走现有的 `is None` 分支。

### 10. `getattr` 的结果没有被收窄

`office.py` 142–145。`left`、`top`、`width`、`height` 来自 `getattr(..., None)`，类型是 `Any | None`。`if None in (left, top, width, height)` 不会让 mypy 把四个名字收成非空，所以 `float(...)` 仍看到 `Any | None`。

改法：改成四个 `is None` 判断，任一为空就返回 `None`。通过之后再 `float(...)`。坐标计算不变。

### 11. OCR 元数据字典被推成 `dict[str, object]`

`office.py` 433、449、455。`metadata` 没有注解，值里同时有 `str` 和 `bool`，mypy 把它推成 `dict[str, object]`。`_make_block` 和 `_extract_presentation_pdf_ocr_text` 的返回类型要求 `dict[str, str | int | bool | None]`。报错文本里的 `str | int | float | None` 是 mypy 把 `bool` 并进 `int` 之后的显示。

改法：给这个字典显式注解 `dict[str, str | int | bool | None]`。键仍是 `parser_name`、`used_ocr`、`ocr_provider`。`result.provider` 已是 `str`。

### 12. Alembic head 可能是 `None`

`app/core/migration.py` 69。`get_head_revision` 标注返回 `str`。`ScriptDirectory.get_current_head()` 的类型是 `str | None`（多头或没有 head 时）。

改法：取出 head 后若是 `None`，抛 `RuntimeError`，说明迁移链没有唯一 head。有 head 时仍返回那个字符串。`get_head_revision` 的返回类型保持 `str`。`stamp` 的参数是 `str`，因此不能把 `None` 传进去。

`auto_migrate` 在开发环境把迁移失败记成警告并继续启动，这段逻辑的 `try` 从 `upgrade("head")` 才开始。`_stamp_if_schema_already_at_head()` 在它之前调用 `get_head_revision()`。head 为 `None` 时如果直接抛错，开发环境会在进入这段 `try` 之前退出。

采用的口径：保留 `get_head_revision()` 的抛错。在 `auto_migrate` 里给 `_stamp_if_schema_already_at_head()` 单独包一层与现有迁移失败相同的处理：开发环境记警告并返回 `False`，生产环境拒绝启动。不要把这次调用挪到现有 `try` 里面，因为那个 `try` 在「没有待迁移就返回」之后，挪进去会让无待迁移时不再做 stamp。`get_pending_migrations` 和补列函数仍留在原来的位置，它们的失败方式不变。

`_cli_check` 也会调用 `get_head_revision()`。head 为 `None` 时检查命令抛出同一个 `RuntimeError`，不走开发环境的启动降级。

## 改动范围

- `app/rag/service.py`
- `app/rag/import_jobs.py`
- `app/rag/vectorstore/local.py`
- `app/rag/vectorstore/milvus.py`
- `app/rag/ocr/tesseract.py`
- `app/rag/document_parsers/libreoffice.py`
- `app/rag/document_parsers/pdf.py`
- `app/rag/document_parsers/office.py`
- `app/rag/chunking/strategies/parent_child.py`
- `app/core/migration.py`
- `app/audit/logger.py`

不改 `app/rag/access.py`、模型字段、mypy 配置、检索融合、权限、软删除和版本状态的业务规则。

## 验收

解释器固定为 conda 环境 `ai-assistant`：`D:\install\anaconda3\envs\ai-assistant\Scripts`。仓库根目录执行。不改用另一套虚拟环境，避免两次结果来自不同解释器。

改业务代码之前，先保存一份全量输出：

```powershell
D:\install\anaconda3\envs\ai-assistant\Scripts\mypy.exe app/ > docs/plans/mypy-app-before.txt
```

该文件只作改前对照，不提交进仓库时可以放在被忽略的位置；若写入 `docs/plans/`，实现说明里标明它是对照稿。全量输出里会仍有本次不修的其他 `.is_` 报错。

改完后：

```powershell
D:\install\anaconda3\envs\ai-assistant\Scripts\mypy.exe app/rag/access.py app/rag/import_jobs.py app/rag/service.py
D:\install\anaconda3\envs\ai-assistant\Scripts\mypy.exe app/ > docs/plans/mypy-app-after.txt
D:\install\anaconda3\envs\ai-assistant\Scripts\pytest.exe tests/test_rag_access.py tests/test_rag_permission_semantics.py tests/test_rag.py tests/test_rag_import_jobs.py tests/test_chunking.py -v
D:\install\anaconda3\envs\ai-assistant\Scripts\ruff.exe check app/rag/service.py app/rag/import_jobs.py app/rag/vectorstore/local.py app/rag/vectorstore/milvus.py app/rag/ocr/tesseract.py app/rag/document_parsers/libreoffice.py app/rag/document_parsers/pdf.py app/rag/document_parsers/office.py app/rag/chunking/strategies/parent_child.py app/core/migration.py app/audit/logger.py
```

针对三个源文件的 mypy、pytest、ruff 退出码都为 0，且不再出现这 40 条。全量 `mypy app/` 仍会因本次不修的其他报错而非 0；对照只要求这 40 条消失，并且不新增别的报错。通过之后再重跑 `EVD-001`，不在本方案里把清点表改成完成。

## 评审补正

对照 `docs/reviews/2026-09-29-plan_mypy_rag_three_files-评审.md`。四条补正都已核对代码后写入上面的改法。

| 编号 | 结论 | 写入位置 |
|---|---|---|
| R-1 | 可行。五个 `col` 的 import 与评审所列当前语句一致。`local.py` 确无 `from __future__ import annotations`，`Sequence` 必须导入。另补 `TypedDict` 与 `Any` 的 import，否则方案 4 和方案 9 在 mypy 里名字未定义。 | 方案 1 的 import 表，方案 3、4、9 |
| R-2 | 采用口径 A。调用链属实：`auto_migrate` 第 344 行调用 `_stamp_if_schema_already_at_head`，`try` 从第 356 行的 `upgrade("head")` 开始；`get_head_revision` 在第 319 行；`stamp` 的参数类型是 `str`。口径 B 不采用：它把返回类型改成 `str \| None`，还要改 319 和 386 两处调用。 | 方案 12 |
| R-3 | 可行。未知键改为抛 `ValueError` 并列出键名，不静默跳过。 | 方案 8 |
| R-4 | 可行。`sqlmodel/orm/session.py` 里 `exec` 对非 `SelectOfScalar` 转调 `execute` 并原样返回；`execute` 返回 `Result[Any]`。`rowcount` 继续用 `getattr`。 | 方案 2 |

## 非目标

不修改 `pyproject.toml` 的 mypy 段。不安装 SQLAlchemy 插件。不清理本次命令没有报出的其他 `.is_` / `.desc()` 调用。不把 Mock Embedding 或检索指标写进这次修复。
