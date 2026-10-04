# 实现说明：其他章节缺口卡入 tasks.yaml

> 日期：2026-10-03
> 范围：只追加任务卡与同步计划文档。未改 `app/`、`frontend/`。未把任何卡改成 `ready`。

## 完成了什么

对照差距表第 2 表与需求第四～六章，将验收已写清、且不在第 4 节已入卡范围的 8 项追加进 `tasks.yaml`，均为 `backlog`。

| 编号 | 标题 | 阶段 |
|---|---|---|
| `CLI-003` | CLI：start / stop / status / logs | 2 |
| `FLOW-004` | 工作流开关与 croniter 缺失提示 | 4 |
| `EVO-002` | Reflect 改技能与待办提取 | 4 |
| `ADM-006` | 租户设置页（ADM-04） | 5 |
| `ADM-007` | Feature Flag 管理 API（ADM-05） | 5 |
| `SEC-007` | 注入检测可阻断 | 5 |
| `NFR-013` | 硬编码 URL 扫描 | 6 |
| `DOC-001` | CHANGELOG 与 Breaking 区 | 6 |

拆分计划：`docs/plans/plan_v1_other_chapters_cards_20261003.md`  
编号对照：`docs/plans/plan_v1_other_chapters_card_ids_20261003.md`

`tasks.yaml` 现为 144 条：70 done / 0 ready / 73 backlog / 1 cancelled。

## 明确没入卡

- 差距表第 3 表（判定未写清）
- 差距表第 4 节（尚未核对）
- `未定`：会话反馈、专家团、四种对话模式
- 独立 Reranker、查询变换（未进锁定阶段 1～7 / 有进入条件）

## 验证

```text
python docs/plans/_gen_v1_other_cards.py
→ appended 8 cards; total 144; backlog 73
```

未跑业务测试（本批不改业务代码）。

## 没做的事

- 未改业务代码
- 未把任一卡改成 `ready`
- 未开工 `QA-005`
