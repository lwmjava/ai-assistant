# PRAG-001 夜间实现计划（2026-10-05 第一夜）

> 任务卡：PRAG-001「检索越权核对：重跑官方集确认现状」
> 状态：夜间第一夜切片；L0 只读核对，不改实现
> 分支：nightly/rag-20261005（HEAD=ab72748，工作区干净）
> 依据：tasks.yaml PRAG-001 卡 + nightly-autonomous-development.md（eligible: true）

## 1. 目标

用现行评测器重跑 rag-v0.1 官方集，出具 safety 切片报告，确认当前真实检索越权案例数（`violation_cases`）。验收结论：`violation_cases` 必须为 0 才能判定本卡可关闭。

## 2. 现状（卡内已固化事实）

- 冻结基线（2026-09-19）的越权 0.1875 出自旧口径（当时 16 例含 forbidden docs），不代表当前仍复现。
- 2026-09-24 官方评测已记录 `violation_cases = 0`（rag-v0.1-rrf-k60 报告）。
- rag-026（holdout）、rag-036/037（acl_planned）三例 `forbidden_document_ids` 均为空数组，属生成层/ACL 计划项，不是待修检索越权。
- ADR-0001 选项 A：同租户当前文档可被检索；生成层不得回答编制编号/绩效规则（生成层拒答另立卡，不在本卡范围）。

## 3. 方案

1. 使用 ai-assistant conda 环境解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。
2. 运行 `python scripts/run_rag_baseline.py`（默认 `--mode official`，真实嵌入，.env 已配置 EMBEDDING_API_KEY）。
3. 脚本重建隔离评测索引（`data/eval_rag_v01.db`）并跑 rag-v0.1 官方集。
4. 输出报告 JSON 到 `evals/reports/rag-v0.1-baseline-20261005.json`（脚本内置拒绝覆盖 2026-09-19 冻结基线）。
5. 解析 `overall.safety.violation_cases` 与 `cases_with_forbidden_docs`，对照验收。
6. 若 `violation_cases > 0`：本卡不得关闭，需输出跨租户命中的独立立项说明（夜间仅记录，不擅自立项）。

## 4. 非目标

- 不实现资源级 ACL（ADR-0001 仍为 Planned，需新 ADR）。
- 不改生成层拒答规则（拒答另立 PRAG-004）。
- 不改检索链路参数、候选集、融合常数。
- 不改写已冻结的 v0.1 基线报告文件。
- 不用 holdout 调参。
- 夜间不 commit、不合并、不部署。

## 5. 验收（来自卡 acceptance）

- [ ] 重跑后 `violation_cases` 必须为 0，本卡才可给出"可关闭"结论（最终关闭由次日人工审查决定）。
- [ ] `violation_cases > 0` 时不得判定完成。
- [ ] 报告可复现，含命令与用例清单。

## 6. 证据清单（执行后回填）

- 执行命令与退出码
- 报告 JSON 路径：`evals/reports/rag-v0.1-baseline-20261005.json`
- `overall.safety` 切片数值
- 用例清单（数据集 `rag-v0.1`，`evals/datasets/rag-v0.1/cases.json`）
