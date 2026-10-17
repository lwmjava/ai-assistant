# RAG-016 父块展开锁定实现说明

任务：RAG-016。日期：2026-10-06（夜间）。状态：backlog + progress（实现与锁定回归完成，待人工验收关闭）。计划：[实施计划](plan_rag_016_parent_expand_lock_20261006.md)。

## 实际行为

知识库搜索（`RAGService.search`）在混合检索后调用 `_expand_parent_chunks`：
- 命中带 `parent_id` 的子块时，返回集含对应父块；子块本身保留（不是「只返回父块」模式）。
- 父块继承子块 `score`/`similarity`/`version_status`，并去重（父子同时命中父块只出现一次）。
- 边界行为（本次新增回归锁定）：
  1. 命中父块本身 → 不向下追加其子块；
  2. 子块 `parent_id` 指向不存在的父块记录 → 安全跳过，不崩溃；
  3. 展开的父块继承子块 `version_status`；
  4. 三层父子链只展开一层，不递归追加祖父块。

对话路径（`make_retriever`/`HybridRetriever`）不经展开——按卡 non_goals，对话父块选入与预算由 RAG-030 承接，本卡未改动。

## 文件

| 文件 | 变更 | 原因 |
|---|---|---|
| `tests/test_rag_parent_expand_lock.py` | 新增 | RAG-016 边界回归锁定（4 项），与 `tests/test_rag.py` 既有 3 项展开测试互补 |
| `docs/plans/plan_rag_016_parent_expand_lock_20261006.md` | 新增 | 本卡实施计划 |
| `docs/plans/implementation_rag_016_parent_expand_lock_20261006.md` | 新增 | 本实现说明 |
| `tasks.yaml` | 修改 | RAG-016 卡加 `progress` 标注（不改 status_values，不标 done） |

未修改业务代码（`app/rag/service.py` 的 `_expand_parent_chunks` 为既有实现，测试验证其行为已满足验收，无需修复）。

## 验证记录

项目解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。测试使用 `init_db()` + `Session(engine)` 隔离测试 DB 与 `data/pytest-tmp`，不运行真实供应商或操作生产数据。

| 实际命令 | 结果 |
|---|---|
| `pytest tests/test_rag_parent_expand_lock.py -q` | 4 passed，退出 0（含修复 col()/断言收窄后复跑） |
| `pytest tests/test_rag.py -k "expand or parent_expand" -q` | 3 passed / 38 deselected，退出 0（既有展开测试未回归） |
| `pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -v` | 82 passed / 1 skipped，退出 0 |
| `ruff check --no-cache tests/test_rag_parent_expand_lock.py` | All checks passed，退出 0 |
| `mypy --cache-dir data/mypy-rag016c tests/test_rag_parent_expand_lock.py` | Success: no issues found，退出 0 |

中途修复记录：首轮 mypy 对 `_native_store` 返回抽象类型报 `_store` 属性缺失（改用 `cast(NativeRagBackend, ...)`）、`parent_id.is_(None)` 与 `DocumentChunk | None` 属性访问报错（改 `col()` + 断言提前收窄）、ruff import 顺序（`typing` 前置）。修复后三项全部 0。

## 未做事项 / 边界

- 未改对话路径（RAG-030 承接）、未做「只返回父块」模式。
- 未改切分策略、Embedding、RRF、阈值；未操作知识库/索引/数据库数据。
- 未新增依赖、未改公共契约、未推送、未合并、未部署。
- 本卡为行为锁定，不宣称检索质量提升；展开行为此前已由 `_expand_parent_chunks` 提供，本夜以测试固化。

## 验收步骤（人工）

1. 读 `tests/test_rag_parent_expand_lock.py` 4 项用例与 `tests/test_rag.py` 3 项既有展开用例，确认覆盖卡验收（子块命中含父块、不只父块、去重）。
2. 复跑：`pytest tests/test_rag_parent_expand_lock.py tests/test_rag.py -k "expand or parent_expand" -q`（预期 7 passed）。
3. 确认 RAG-016 卡 `progress` 标注后，人工决定标 `done`。
