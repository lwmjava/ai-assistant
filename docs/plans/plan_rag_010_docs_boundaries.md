# RAG-010：补记生效日期全量载入、工具名边界和 critique 预算

> 状态：已完成（`tasks.yaml` RAG-010 `done`，2026-09-24）
> 分支：`fix/rag-005-006-review`
> 来源：评审修复方案阶段 1 / M2、N1、N2
> 日期：2026-09-24

只把三处已存在行为写进文档和函数说明。不改检索查询，不改拒绝条件，不改管线拼接。阶段 2 的控制面权限（L2）不在本任务。

## 目标

1. [`docs/adr/0002-official-vectorstore.md`](../adr/0002-official-vectorstore.md) §6：`RAG_EFFECTIVE_DATE_FILTER` 默认关闭；打开后 Local 按租户全量载入再过滤；检索面与控制面不能共用 `is_current`；生产打开前另立性能项。写明本任务不改代码。
2. [`docs/AI辅助开发迭代指导.md`](../AI辅助开发迭代指导.md) §2.3、§4.1：拒工具以 `[UNTRUSTED_SOURCE]` 为开关，对整段 context 做子串匹配，不是通用白名单；critique 在字符预算截断之后追加，上限是 `AGENT_MAX_REVISIONS`。
3. [`app/rag/context_merge.py`](../../app/rag/context_merge.py) 中 `reject_untrusted_tool_call` 的函数说明与实现一致，函数体不变。

## 非目标

不改 `local.py` 查询，不实现版本状态机，不改拒绝条件和 pipeline 拼接，不改 Milvus，不实施 L2 权限与审计。

## 实施清单

| 项 | 内容 | 状态 |
|---|---|---|
| adr-m2 | ADR-0002 性能节补记全量载入与两条路径 | done |
| guide-n1-n2 | 迭代指导补记拒工具边界与 critique 预算 | done |
| docstring | `reject_untrusted_tool_call` 仅更新函数说明 | done |
| close | `tasks.yaml` 的 RAG-010 标为 done | done |

## 验收

```powershell
pytest tests/test_context_merge.py -v
ruff check app/rag/context_merge.py
mypy app/rag/context_merge.py
```

## 风险与回滚

L0，仅文档与 docstring。回滚是还原 ADR、迭代指导、函数说明和 `tasks.yaml`。
