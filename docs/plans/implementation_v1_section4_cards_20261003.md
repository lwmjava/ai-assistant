# 实现说明：1.0 锁定第 4 节新卡入 tasks.yaml

> 日期：2026-10-03
> 范围：只追加任务卡与同步计划文档。未改 `app/`、`frontend/`。未把任何卡改成 `ready`。

## 完成了什么

把 `docs/plans/plan_v1_task_split_20261002.md` 第 4 节原先无卡号的工作，全部追加进 `tasks.yaml`，并赋予稳定编号。共 28 张，状态均为 `backlog`。

Vitest 与 ESLint 仍并入既有 `NFR-001`，不单开卡。

编号对照：`docs/plans/plan_v1_section4_card_ids_20261003.md`。

| 阶段 | 新卡 |
|---|---|
| 1 | `COV-001` |
| 2 | `ENV-001`、`ENV-002`、`INST-004`、`CLI-002`、`NFR-012`、`API-001` |
| 3 | `SSE-001`、`RAG-014`、`RAG-015`、`CHAT-006`、`LLM-001`、`CHAT-007` |
| 4 | `FLOW-003`、`EVO-001` |
| 5 | `AUTH-005`、`FE-001`、`SEC-005`、`INV-004`、`QUOTA-002`、`CHAT-008`、`EXPORT-002`、`AUTH-006`、`ADM-005` |
| 6 | `MIG-001`、`SAND-003`、`SEC-006` |
| 7 | `REL-005` |

`tasks.yaml` 现为 136 条：70 done / 0 ready / 65 backlog / 1 cancelled。

## 代码位置

- `tasks.yaml`：追加 28 条任务定义
- `docs/plans/plan_v1_section4_card_ids_20261003.md`：编号对照表
- `docs/plans/plan_v1_task_split_20261002.md`：第 2、4、5 节改为引用卡号
- `docs/plans/plan_v1_lock_20261002.md`：阶段 1～7 表与第 11 节改为引用卡号
- `AGENTS.md`、`docs/AI辅助开发迭代指导.md`：任务总数与 backlog 数同步

## 验证

```text
python -c "import yaml; ..."
→ tasks 136；backlog 65；28 个新 id 均存在；missing []
```

未跑 pytest、ruff、mypy、前端构建（本批不改业务代码）。

## 没做的事

- 未把任一新卡或企业卡改成 `ready` / `in_progress`
- 未改业务代码
- 未开工 `QA-005`
- 未重跑 pytest
