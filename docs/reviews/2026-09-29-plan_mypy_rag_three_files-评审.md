# 评审：`docs/plans/plan_mypy_rag_three_files.md`（三个源文件 mypy 40 条报错）

> 日期：2026-09-29
> 评审对象：`docs/plans/plan_mypy_rag_three_files.md`
> 评审方式：原样复现命令 + 临时探针文件验证改法（探针已删除，未改动任何业务代码）
> 评审结论：**通过，附 4 条补正意见**。40 条报错全部属实，12 条改法全部有效。

---

## 1. 结论

| 评审项 | 结论 | 说明 |
|---|---|---|
| 40 条报错是否真实存在 | **属实** | 原样复现，行号与错误码 1:1 对上，无虚构、无遗漏 |
| 29 / 11 的拆分是否准确 | **准确** | 三源文件 11 条，跟随导入 29 条；`access.py` 确实 0 error |
| 12 条改法是否可行 | **全部可行** | 逐条写进探针跑 mypy，探针自身 0 error |
| 计数是否自洽 | **自洽** | 方案 1 覆盖 17 条 + 方案 2–12 覆盖 23 条 = 40 |
| 是否可直接开工 | **否，需先补正** | 缺 import 步骤，2 处口径需用户确认 |

**4 条补正意见**：

| 编号 | 级别 | 摘要 |
|---|---|---|
| R-1 | P0 | 方案漏写 import 步骤，照做会 `NameError` |
| R-2 | P1 | 方案 12 有启动期行为回退风险，需确认口径 |
| R-3 | P1 | 方案 8 把"抛错"改成"静默忽略"，建议显式报错 |
| R-4 | P2 | 方案 2 存在零 `type: ignore` 的更优解 |

---

## 2. 复现环境与原样输出

按方案声明的环境执行：

```powershell
D:\install\anaconda3\envs\ai-assistant\Scripts\mypy.exe app/rag/access.py app/rag/import_jobs.py app/rag/service.py
```

实测环境（本轮核对所得）：

| 项 | 值 |
|---|---|
| mypy | 1.14.1（compiled） |
| ruff | 0.8.4 |
| Python | 3.13（conda `ai-assistant`） |
| sqlmodel | 0.0.22 |
| SQLAlchemy | 2.0.36 |
| mypy 配置 | `python_version=3.11`、`ignore_missing_imports=true`、`show_error_codes=true`、**`warn_unused_ignores=false`** |

输出尾部与方案一致：

```
Found 40 errors in 11 files (checked 3 source files)
EXIT=1
```

> 提示：`warn_unused_ignores=false` 意味着**多余的 `type: ignore` 不会被 mypy 报出来**。审查 ignore 是否有效不能依赖 mypy 提示，必须人工比对错误码。方案 2 就是在这一点上发现问题的。

---

## 3. 40 条报错逐条核对

图例：✅ 属实（文件、行号、错误码与实际输出一致）

### 3.1 另外 29 条（跟随导入）

| # | 位置 | 错误码 | 核对 |
|---|---|---|---|
| 1 | `ocr/tesseract.py:216` | `arg-type` stdout `bytes \| None` | ✅ |
| 2 | `ocr/tesseract.py:217` | `arg-type` stderr `bytes \| None` | ✅ |
| 3 | `document_parsers/libreoffice.py:97` | `assignment` | ✅ |
| 4 | `document_parsers/libreoffice.py:101` | `arg-type` | ✅ |
| 5 | `chunking/strategies/parent_child.py:40` | `arg-type`（`str \| None`） | ✅ |
| 6 | `chunking/strategies/parent_child.py:40` | `arg-type`（`dict \| None`） | ✅ |
| 7 | `document_parsers/pdf.py:26` | `misc` Cannot assign to a type | ✅ |
| 8 | `document_parsers/pdf.py:26` | `assignment` | ✅ |
| 9 | `vectorstore/local.py:149` | `arg-type` join | ✅ |
| 10 | `vectorstore/local.py:152` | `union-attr` Item "datetime" | ✅ |
| 11 | `vectorstore/local.py:152` | `union-attr` Item "None" | ✅ |
| 12 | `vectorstore/local.py:154` | `attr-defined` bool 无 `is_` | ✅ |
| 13 | `vectorstore/local.py:159` | `arg-type` Sequence vs list | ✅ |
| 14 | `document_parsers/office.py:29` | `assignment` Presentation | ✅ |
| 15–18 | `document_parsers/office.py:142–145` | `arg-type` float | ✅ |
| 19 | `document_parsers/office.py:433` | `dict-item` | ✅ |
| 20 | `document_parsers/office.py:449` | `dict-item` | ✅ |
| 21 | `document_parsers/office.py:455` | `return-value` | ✅ |
| 22 | `vectorstore/milvus.py:213` | `arg-type` join | ✅ |
| 23 | `vectorstore/milvus.py:216` | `union-attr` Item "datetime" | ✅ |
| 24 | `vectorstore/milvus.py:216` | `union-attr` Item "None" | ✅ |
| 25 | `vectorstore/milvus.py:218` | `attr-defined` | ✅ |
| 26 | `vectorstore/milvus.py:221` | `arg-type` Sequence vs list | ✅ |
| 27 | `core/migration.py:69` | `return-value` `str \| None` → `str` | ✅ |
| 28 | `audit/logger.py:151` | `arg-type` where | ✅ |
| 29 | `audit/logger.py:152` | `call-overload` | ✅ |

### 3.2 三个源文件上的 11 条

| # | 位置 | 错误码 | 核对 |
|---|---|---|---|
| 30 | `rag/service.py:50` | `attr-defined` `is_.is_` | ✅ |
| 31 | `rag/service.py:449` | `attr-defined` `is_.is_` | ✅ |
| 32 | `rag/service.py:454` | `union-attr` Item "datetime" | ✅ |
| 33 | `rag/service.py:454` | `union-attr` Item "None" | ✅ |
| 34 | `rag/service.py:457` | `attr-defined` datetime 无 `desc` | ✅ |
| 35 | `rag/import_jobs.py:375` | `attr-defined` | ✅ |
| 36 | `rag/import_jobs.py:383` | `attr-defined` | ✅ |
| 37 | `rag/import_jobs.py:440` | `assignment` `str \| None` → `str` | ✅ |
| 38 | `rag/import_jobs.py:492` | `arg-type` version_group_id | ✅ |
| 39 | `rag/import_jobs.py:493` | `arg-type` int(...) | ✅ |
| 40 | `rag/import_jobs.py:535` | `attr-defined` datetime 无 `asc` | ✅ |

### 3.3 计数自洽性

方案 1 覆盖 **17 条**：service 5（50、449、454×2、457）+ import_jobs 3（375、383、535）+ local 4（149、152×2、154）+ milvus 4（213、216×2、218）+ audit 1（151）。

方案 2–12 覆盖 **23 条**：1(#2) + 2(#3) + 2(#4) + 1(#5) + 2(#6) + 2(#7) + 2(#8) + 3(#9) + 4(#10) + 3(#11) + 1(#12)。

17 + 23 = 40，与总数一致。

---

## 4. 12 条改法验证

### 4.1 验证方法

不是靠阅读判断，而是把每条改法**原样写进临时探针文件**跑 mypy。探针文件放在仓库根目录，验证后立即删除（`git status` 确认无残留）。

判读规则：探针文件自身若报错，说明该改法不成立；跟随导入进来的 `app/` 模块报错属预期（那些模块尚未修改）。

**结果：探针文件自身 0 error，12 条改法全部成立。**

### 4.2 逐条结论

| 方案 | 改法 | mypy 验证 | 备注 |
|---|---|---|---|
| 1 | `col()` 包装列表达式 | ✅ | `.is_()` / `.desc()` / `.asc()` / `==` / `<` 全部通过，含 `delete().where()` |
| 2 | `type: ignore[arg-type]` → `[call-overload]` | ✅ | 错误码判断正确 |
| 3 | `rows: Sequence[DocumentChunk]` + `list(rows)` | ✅ | 返回类型仍为 `list[DocumentChunk]`，两个调用点无需包 `list()` |
| 4 | `TypedDict` 描述版本字典 | ✅ | 可去掉 `int(...)` 强转 |
| 5 | `source_ref: str \| None` 前置声明 | ✅ | |
| 6 | 超时分支 bytes 解码 | ✅ | `isinstance(..., bytes)` 后解码，`None` 仍传 `None` |
| 7 | 超时分支改名 `command_text` | ✅ | 不再复用 `command` 变量 |
| 8 | 显式构造 `ChunkParams` | ✅ | **且不需要任何 `type: ignore`** |
| 9 | `PdfReader / Presentation: Any = None` | ✅ | 调用处与 `is None` 分支不变 |
| 10 | 四个 `is None` 替代 `None in (...)` | ✅ | |
| 11 | metadata 显式注解 | ✅ | 433、449、455 三条一并消除 |
| 12 | head 为 `None` 抛 `RuntimeError` | ✅ | 行为影响见 R-2 |

### 4.3 方案依据的事实核对

| 方案中的主张 | 核对结果 |
|---|---|
| 仓库已有 `from sqlmodel import col` 的写法 | ✅ `app/services/admin_users.py:3`、`app/api/routes/audit.py:15` |
| `col()` 能让 mypy 认列方法 | ✅ sqlmodel 0.0.22 签名 `(column_expression: _T) -> Mapped[_T]` |
| tesseract 成功路径 / libreoffice 超时路径已用 `decode` | ✅ `libreoffice.py:102–103` 已在用 |
| `session.exec` 的 `type: ignore[arg-type]` 盖不住 `call-overload` | ✅ mypy 明确输出 `Error code "call-overload" not covered by "type: ignore" comment` |
| 测试里 `child_params` 只覆盖 `chunk_size` | ✅ `tests/test_chunking.py:113` |
| `get_head_revision` 调用方期望 `str` | ✅ `migration.py:319`、`migration.py:386` |

---

## 5. 补正意见

### R-1（P0）方案漏写 import 步骤

方案的改动表只列了调用点，没有列 import。照着做会 `NameError`。

需要补的 import：

| 文件 | 当前 | 需改为 |
|---|---|---|
| `app/rag/service.py:20` | `from sqlmodel import Session, select` | 加 `col` |
| `app/rag/import_jobs.py:14` | `from sqlmodel import Session, select` | 加 `col` |
| `app/rag/vectorstore/local.py:18` | `from sqlmodel import Session, select` | 加 `col` |
| `app/rag/vectorstore/milvus.py:17` | `from sqlmodel import Session, select` | 加 `col` |
| `app/audit/logger.py:18` | `from sqlmodel import Session, delete, select` | 加 `col` |

另外：`app/rag/vectorstore/local.py` **没有** `from __future__ import annotations`，方案 3 用到的 `Sequence[DocumentChunk]` 是运行时求值的注解，必须补 `from collections.abc import Sequence`。

### R-2（P1）方案 12 有启动期行为回退风险

`get_head_revision()` 的调用链：

```
auto_migrate()               migration.py:333
└─ _stamp_if_schema_already_at_head()   migration.py:344   ← 在 try/except 之外
   └─ get_head_revision()               migration.py:319
```

`auto_migrate()` 的 `try` 从 **第 356 行**才开始（包住 `upgrade("head")`），`_stamp_if_schema_already_at_head()` 在它之外，异常不会被 362 行的 `except` 捕获。

行为对比：

| 场景 | 现状 | 改成抛 `RuntimeError` 后 |
|---|---|---|
| head 为 `None`（多 head / 空迁移链）+ 开发环境 | 静默不 stamp，继续启动 | **启动崩溃** |
| 同上 + 生产环境 | 后续 `stamp(None)` 大概率失败 | 启动崩溃（本来也要拒绝启动） |

`auto_migrate()` 的 docstring 与 342、369 行明确写了"开发环境迁移失败不阻塞启动"。改成抛错会让开发环境失去这个降级能力。

两个可选口径，需用户确认：

- **口径 A（推荐）**：保留抛错，但把 `_stamp_if_schema_already_at_head()` 纳入 `auto_migrate()` 的 `try`，让开发环境回到"记错误 + 继续启动"。多 head 本来就是坏迁移链，早失败的方向是对的，只是要保住 dev 降级。
- **口径 B**：`get_head_revision()` 改为返回 `str | None`，由 319、386 两处调用方判空。改动面更大（`stamp(head)` 的入参类型也要处理），但完全不引入新的抛出路径。

### R-3（P1）方案 8 把"抛错"变成"静默忽略"

现状 `ChunkParams(**child_merged)` 遇到未知键会 `TypeError`；方案改为"未知键跳过"，等于把显式失败改成静默降级。方案里"未知键跳过，避免把现在会抛错的输入变成静默成功以外的新行为"这句表述自相矛盾——它**就是**新行为。

影响面评估：**目前很小**。`child_params` 全仓只在 `tests/test_chunking.py:113` 出现，`app/` 内没有配置或 API 写入路径，暂无外部输入源。

建议（择一）：

- 显式拒绝未知键，抛 `ValueError` 并列出键名 —— 与现状同为 fail-fast，行为最接近；
- 或至少 `logger.warning` 记录被跳过的键。

### R-4（P2）方案 2 存在零 `type: ignore` 的更优解

方案称"这是 40 条里唯一保留 `type: ignore` 的地方"。实测存在完全不需要 ignore 的写法：

```python
stmt = delete(AuditLog).where(col(AuditLog.created_at) < cutoff)
result = session.execute(stmt)          # 不需要 ignore
deleted = getattr(result, "rowcount", 0) or 0   # 保持现状
```

依据：SQLModel 的 `Session.exec` 对非 `SelectOfScalar` 语句本就只是转调 `execute` 并原样返回结果，对 `Delete` 而言两者运行时行为一致。

**一个坑**：`result.rowcount` 直接访问会报 `Result[Any] has no attribute rowcount`（实测），所以必须保留 `getattr(result, "rowcount", 0)`——当前代码已经是 `getattr`，不用动。

采纳后 40 条可做到**零 `type: ignore`**。是否采纳由用户决定，不采纳则按原方案保留一处 `call-overload` ignore 亦可。

---

## 6. 验收部分核对

方案列出的 5 个测试文件**均存在**（`tests/test_rag_access.py`、`test_rag_permission_semantics.py`、`test_rag.py`、`test_rag_import_jobs.py`、`test_chunking.py`），ruff 0.8.4 在 conda `ai-assistant` 环境可用，命令本身可行。

两点建议：

1. **写明解释器**。方案里的 `pytest` 未指定解释器。项目测试 venv 为 `C:/Users/Administrator/.workbuddy/binaries/python/envs/ai-assistant-phase0/Scripts/python.exe`，conda `ai-assistant` 亦可。建议写死一个，避免两次运行落在不同环境。
2. **增加全量基线 diff**。方案改了 `visible_chunks_with_status` 这类跨模块签名，存在牵连风险。建议改为：改前、改后各存一份 `mypy app/` 输出做 diff，确认**只减不增**。（注意：全量跑必然仍有其他 `.is_` 类报错，这属于方案"非目标"已声明的范围，diff 的目的只是确认没有新增。）

---

## 7. 边界确认

- 方案声明"这不是 `EVD-001`"：**成立**。`tasks.yaml` 中 `EVD-001` 的 `forbidden_paths` 明确含 `app/`，本方案要改 11 个 `app/` 文件，不能在 `EVD-001` 内做。顺序应为：先落地本方案 → 再重跑 `EVD-001`。
- 方案的"非目标"（不改 mypy 配置、不装 SQLAlchemy 插件、不顺手清理其他 `.is_`）与上述边界一致，评审无异议。
- 本次评审**未修改任何业务代码**，探针文件已删除，工作区无评审引入的变更。

---

## 8. 建议下一步

1. 按 R-1 补齐 import 步骤（P0，硬缺口）。
2. 就 R-2、R-3 二选一定口径（P1）。
3. R-4 由用户决定是否采纳（P2）。
4. 补正落进方案后，按方案第"验收"节执行；通过后再重跑 `EVD-001`。
