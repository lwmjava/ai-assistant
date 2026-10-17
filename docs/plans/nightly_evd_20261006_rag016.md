# 夜间证据包 2026-10-06 — RAG-016 父块展开锁定

分支：`nightly/rag-20261005`（HEAD=5ab382e，工作区含未提交历史改动，夜间不 commit/merge/push）。

## 任务与验收

- 卡：RAG-016「父块展开：命中子块追加父块（方案 A）」，P1 / L1，depends_on=[]。
- 验收：命中带 parent_id 的子块时返回集含对应父块；不以「仅父块」为合格线；回归覆盖展开与去重。
- 状态：backlog → 已加 `progress` 标注（不改 status_values，不标 done，待人工验收）。

## 实际修改文件

| 文件 | 变更 |
|---|---|
| `tests/test_rag_parent_expand_lock.py` | 新增：4 项边界回归锁定 |
| `docs/plans/plan_rag_016_parent_expand_lock_20261006.md` | 新增：实施计划 |
| `docs/plans/implementation_rag_016_parent_expand_lock_20261006.md` | 新增：实现说明 |
| `tasks.yaml` | 修改：RAG-016 卡加 progress 标注 |

业务代码未改动（`_expand_parent_chunks` 为既有实现，测试验证已满足验收）。

## 命令与退出码

| 命令 | 结果 | 退出码 |
|---|---|---|
| pytest tests/test_rag_parent_expand_lock.py -q | 4 passed | 0 |
| pytest tests/test_rag.py -k "expand or parent_expand" -q | 3 passed / 38 deselected | 0 |
| pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -v | 82 passed / 1 skipped | 0 |
| ruff check --no-cache tests/test_rag_parent_expand_lock.py | All checks passed | 0 |
| mypy --cache-dir data/mypy-rag016c tests/test_rag_parent_expand_lock.py | no issues | 0 |
| yaml.safe_load(tasks.yaml) + ID 唯一 | 通过 | 0 |

中途修复（记录）：mypy 抽象属性/`col()`/断言收窄 3 类错误与 ruff import 顺序，修复后全部 0。

## 未做事项

- 未改对话路径（RAG-030 承接）、未做只返回父块模式。
- 未改切分/Embedding/RRF/阈值；未操作知识库/索引/数据库数据。
- 未新增依赖、未改公共契约；未推送/合并/部署。
- 不宣称检索质量提升（本卡为行为锁定）。

## 明早人工审查清单

1. **RAG-025（P0, in_review）人工业务验收**（最高优先）：页面来源正文仍显示、后台 sources 仅 `source_count=N`；验收步骤见 `docs/plans/implementation_rag_025_log_minimization_20261005.md` 第 51-57 行；其实现/计划/独立审查/测试均为未跟踪文件，验收通过后随提交入库。
2. **RAG-016 验收**：复跑 `pytest tests/test_rag_parent_expand_lock.py tests/test_rag.py -k "expand or parent_expand" -q`（预期 7 passed），确认后可将 RAG-016 标 done。
3. **工作区未提交改动**：约 80+ 文件（RAG-025 实现、RAG-014 清洗补偿、tasks.yaml 引号治理等）需在终端提交；AI 侧 git 写曾被权限拦截。
