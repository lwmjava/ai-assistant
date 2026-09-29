# 实现说明：三个源文件 mypy 40 条报错

> 日期：2026-09-29
> 方案：`docs/plans/plan_mypy_rag_three_files.md`
> 解释器：`D:\install\anaconda3\envs\ai-assistant\Scripts`，Python 3.12.0，mypy 1.14.1，pytest 8.3.4，ruff 0.8.4

## 完成的行为

同一条 `mypy app/rag/access.py app/rag/import_jobs.py app/rag/service.py` 现在退出码为 0。查询里的列比较和排序改用 `sqlmodel.col`。`delete` 走 `session.execute`。版本判定用 `TypedDict`。可选导入、子进程超时输出、数据类构造和 Alembic head 按方案改完。

运行时行为：

- 知识库列表、检索过滤、导入去重和审计清理的条件与以前相同，只是类型检查能认出列对象。
- 父子切分的已知字段覆盖保持。`child_params` 里出现 `ChunkParams` 没有的键时抛 `ValueError`，消息带键名。以前这种输入在 `ChunkParams(**dict)` 时抛 `TypeError`。现有测试只覆盖 `chunk_size`，不会走到未知键。
- 迁移链没有唯一 head 时，`get_head_revision()` 抛 `RuntimeError`。`auto_migrate()` 在调用 `_stamp_if_schema_already_at_head()` 时接住它：开发环境记警告并返回 `False`，生产环境拒绝启动。检查命令 `_cli_check` 仍会把这个错误抛出去。
- 文件导入在 `source_name` 为空时仍把 `None` 传给去重函数。

## 代码位置

- `app/rag/service.py`、`app/rag/import_jobs.py`、`app/rag/vectorstore/local.py`、`app/rag/vectorstore/milvus.py`：`col()`。
- `app/rag/vectorstore/local.py`：`visible_chunks_with_status` 接收 `Sequence`，提前返回 `list(rows)`。
- `app/rag/import_jobs.py`：`Versioning`，`source_ref: str | None`。
- `app/audit/logger.py`：`session.execute`，`col(AuditLog.created_at)`。
- `app/rag/ocr/tesseract.py`、`app/rag/document_parsers/libreoffice.py`、`pdf.py`、`office.py`。
- `app/rag/chunking/strategies/parent_child.py`：`_chunk_params_for_child`。
- `app/core/migration.py`：空 head 抛错，并包住 stamp 调用。

## 验证

改前 `mypy app/`：退出码 1，`Found 84 errors in 31 files (checked 158 source files)`。输出在本机临时文件，未写入仓库。

改后：

| 命令 | 退出码 | 结果 |
|---|---|---|
| `mypy app/rag/access.py app/rag/import_jobs.py app/rag/service.py` | 0 | `Success: no issues found in 3 source files` |
| `mypy app/` | 1 | `Found 44 errors in 20 files (checked 158 source files)` |
| `pytest tests/test_rag_access.py tests/test_rag_permission_semantics.py tests/test_rag.py tests/test_rag_import_jobs.py tests/test_chunking.py -v` | 0 | 91 passed，1 warning，69.67s |
| 方案所列 `ruff check` | 0 | `All checks passed!` |

全量 mypy 对照：去掉 40 条，新增 0 条。84 − 40 = 44，与改后条数一致。剩下的 44 条是这次方案声明不修的其他 `.is_` / `.desc()` 调用。

ruff 第一次还报了三处，都不是这 40 条类型错误本身：`audit/logger.py` 里未使用的 `select`、文件末尾缺少换行，以及 `migration.py` 原有的 import 排序。前两处改在已经要动的导入和文件结尾上。`migration.py` 用 `ruff check --fix` 只调整了 import 顺序。之后同一条 ruff 命令退出码为 0。

## 没做的事

没有重跑 `EVD-001`。没有把清点表第 1–3 行改成完成。没有修改 `tasks.yaml`。没有放宽 mypy 配置，没有清理其余 44 条报错。没有改模型字段、检索融合、权限、软删除和版本状态的业务规则。
