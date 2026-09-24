# RAG-011：控制面权限、审计与单一当前版

> 状态：已拆入 `tasks.yaml`（RAG-011 `ready`）
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
