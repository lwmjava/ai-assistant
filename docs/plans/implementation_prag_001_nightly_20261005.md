# PRAG-001 夜间实现说明（2026-10-05 第一夜）

> 任务卡：PRAG-001「检索越权核对：重跑官方集确认现状」
> 分支：nightly/rag-20261005（HEAD=ab72748）
> 状态：核对完成，验收条件满足，待次日人工审查后关闭

## 1. 完成了什么

用现行评测器重跑 rag-v0.1 官方集，出具 safety 切片报告，确认当前真实检索越权案例数。

- **结论**：`violation_cases = 0 / 13`（13 例含 forbidden_document_ids 的案例全部未越权命中），`violation_rate = 0.0`。
- **验收对照**（卡 acceptance）：violation_cases = 0 ✅，报告可复现（含命令）✅，未判定"完成"（关闭留人工审查）。

## 2. 实际修改文件（本次夜间新增，未改动任何业务代码）

| 文件 | 性质 |
|---|---|
| `docs/plans/plan_prag_001_nightly_20261005.md` | 实现计划（先落盘后执行） |
| `docs/evaluations/rag-v0.1-safety-slice-20261005.md` | safety 切片核对报告（卡 deliverables） |
| `docs/plans/implementation_prag_001_nightly_20261005.md` | 本实现说明（证据包） |
| `tasks.yaml`（PRAG-001 execution.nightly.progress） | 进度标注（不改 status_values） |
| `evals/reports/rag-v0.1-baseline-20261004.json` | 评测器自动写出的新报告（未覆盖冻结基线） |

## 3. 验证命令与结果

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python scripts/run_rag_baseline.py`（ai-assistant 解释器） | 0 | 文档 13 篇/分块 37；Recall@1=0.8095 @5=0.9619 @10=0.9619 MRR=0.9429；**越权命中案例 0/13**；P50=291.4ms P95=342.3ms；失败案例 3 条 |

报告路径：`evals/reports/rag-v0.1-baseline-20261004.json`
运行配置：official / OpenAICompatibleEmbeddingProvider / text-embedding-v3 / 1024 维 / local / native / structured 500-64 / rrf_k=60 / 深度 10（与冻结基线口径一致）

## 4. 明确没做的事（Non-goals 边界）

- **未实现**资源级 ACL（ADR-0001 仍为 Planned，需新 ADR，非本卡）。
- **未改**生成层拒答规则（拒答问题另立卡 PRAG-004）。
- **未改**任何检索链路参数、候选集、融合常数。
- **未改写**已冻结基线 `rag-v0.1-baseline-20260919.json`（脚本内置拒绝覆盖）。
- **未用** holdout 调参。
- **未修** 3 条失败案例（rag-031/040/041，均属召回/引用覆盖，与越权无关；PRAG-001 非目标禁止改检索参数）。
- **未 commit、未合并、未部署、未推送**（夜间红线）。

## 5. 残余风险与说明

- 3 条失败案例（rag-031 effective_date 召回、rag-040/041 prompt_injection 引用覆盖）不在本卡范围，供后续相关卡（如 RAG-016/PRAG-004）参考。
- 本卡只核对检索层越权，生成层/ACL 计划项（rag-026/036/037）由 PRAG-004 与 ADR-0001 后续承载。

## 6. 次日人工审查清单

1. 复核报告 `evals/reports/rag-v0.1-baseline-20261004.json` 的 `overall.safety`（violation_cases=0）。
2. 复跑命令确认可复现。
3. 确认 3 条失败案例与越权无关的判定。
4. 决定 PRAG-001 关闭；如需，把失败案例转给相关卡。
