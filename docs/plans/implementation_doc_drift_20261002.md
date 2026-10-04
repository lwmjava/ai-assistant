# 实现说明：纠正仍被当作当前事实的进度段落

> 日期：2026-10-02
> 结果：交付排期、剩余交付拆分、能力矩阵、迭代指导、C6 计划文首和 `AGENTS.md` 里过时的「80% 未达到 / 五条未执行 / B3–D4 尚未实现」已改成与 `tasks.yaml` 和清点记录一致。点状实现说明和评审记录保留当时的结果。

## 完成的行为

读者打开这些入口时，当前结论是：

- 交付排期第 1–16 项于 2026-09-29 清点为 16/16，80% 达到。
- Milvus 五条脚本于 2026-09-29 通过。默认向量库仍是 `local`，不标 `Implemented`。
- 第 17–20 项任务卡在 2026-10-02 为 `done`。发布门禁未达到发布合格。词面覆盖重排不采纳。
- `tasks.yaml` 为 108 条：70 done / 0 ready / 37 backlog / 1 cancelled（`PRAG-002`）。

各节里拆分或实现前的「现状」仍保留，并标明那是当天的边界，不是现在的结论。

## 代码位置

只改文档：

- `docs/plans/plan_delivery_2027-03-25.md` 文首、第 6 节、第 7 节
- `docs/plans/plan_remaining_delivery.md` 文首、拆分时现状、SEC-003、SEC-004、D3 四条的状态句
- `docs/plans/plan_sec_c6.md` 文首、计划时现状、SEC-004 结果句
- `docs/product/as-is-capability-matrix.md` 对账日期、Milvus、重排、引用、评测、记忆、ADR-0002、验证命令
- `docs/AI辅助开发迭代指导.md` 文首、第 4 节、第 5 节、第 13 节
- `AGENTS.md` 第 14 节中「B3 至 D4 尚未完成」、测评大盘进入条件，以及 `PRAG-002` 已取消

## 验证

对照 `tasks.yaml` 状态计数（70 / 37 / 1）、`docs/plans/record_delivery_16.md`、`docs/plans/implementation_evd_004_index_params.md`、`docs/plans/implementation_rel_003.md`、`docs/plans/implementation_rel_004.md`。没有重跑 pytest、ruff 或 mypy。

## 没做的事

没有改写 `docs/plans/implementation_sec_003.md`、`implementation_sec_004.md`、`implementation_evd_001.md` 至 `implementation_evd_003.md`。这些文件记录的是补证之前的当次结果。没有改写 `docs/reviews/` 里「计划尚未实现」的评审记录。没有改业务代码，没有改 `tasks.yaml`。
