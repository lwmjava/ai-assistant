"""功能概述
根据调参集上的检索汇总，决定倒数排名融合常数是否替换当前对照值。

功能涵盖
- 对照与候选的 Recall@1、MRR、越权案例数比较。
- 多个合格候选时按 Recall@1、MRR 择优。

已完成
- 两项主指标都严格提高，且越权案例数不增加，才允许替换。
- 只提高一项、持平、主指标回退或越权变差时保持对照。
- 本模块不读取 holdout，避免用留出集选参数。

待完善
- 尚未把引用覆盖率或延迟纳入采纳条件。
- 汇总字典依赖评测夹具的字段名，字段变化时需要同步测试。
"""

from __future__ import annotations

from typing import Any

TUNING_SPLITS = frozenset({"development", "validation"})


def choose_k(
    control_k: int,
    control_summary: dict[str, Any],
    candidates: list[tuple[int, dict[str, Any]]],
) -> dict[str, Any]:
    """在调参汇总上选择融合常数。

    作用：只根据 development 与 validation 的汇总决定保持对照，或采纳一个候选。
    入参：
    - control_k：对照常数，当前为 60。
    - control_summary：对照的汇总，字段与评测夹具 summarize 一致，至少含
      recall['@1']、mrr、safety.violation_cases。
    - candidates：候选列表，每项为 (常数, 同结构汇总)。不要传入 holdout 汇总。
    出参：字典，含 adopted_k、decision（keep 或 adopt）、reason、candidates。
    """
    notes: list[dict[str, Any]] = []
    eligible_rows: list[tuple[int, dict[str, Any]]] = []
    for rrf_k, summary in candidates:
        ok, reason = _judge(control_summary, summary)
        notes.append(
            {
                "rrf_k": rrf_k,
                "eligible": ok,
                "reason": reason,
                "recall@1": summary["recall"]["@1"],
                "mrr": summary["mrr"],
                "safety_violation_cases": summary["safety"]["violation_cases"],
            }
        )
        if ok:
            eligible_rows.append((rrf_k, summary))

    if not eligible_rows:
        return {
            "adopted_k": control_k,
            "decision": "keep",
            "reason": "没有候选同时提升 Recall@1 与 MRR 且不增加越权案例，保持对照 k",
            "candidates": notes,
        }

    best_k, _summary = max(
        eligible_rows,
        key=lambda item: (float(item[1]["recall"]["@1"]), float(item[1]["mrr"])),
    )
    return {
        "adopted_k": best_k,
        "decision": "adopt",
        "reason": f"采纳 k={best_k}：调参集上 Recall@1 与 MRR 都更高，且越权案例未增加",
        "candidates": notes,
    }


def _judge(control: dict[str, Any], candidate: dict[str, Any]) -> tuple[bool, str]:
    """判断单个候选是否允许替换对照。

    作用：同时检查两项主指标和越权案例数。
    入参：control、candidate 均为 summarize 形状的汇总。
    出参：(是否合格, 原因码)。合格时原因码为 adopt。
    """
    if candidate["safety"]["violation_cases"] > control["safety"]["violation_cases"]:
        return False, "safety_regression"
    control_recall = control["recall"]["@1"]
    candidate_recall = candidate["recall"]["@1"]
    control_mrr = control["mrr"]
    candidate_mrr = candidate["mrr"]
    if None in (control_recall, candidate_recall, control_mrr, candidate_mrr):
        return False, "missing_primary_metric"
    if candidate_recall < control_recall or candidate_mrr < control_mrr:
        return False, "primary_regression"
    if candidate_recall > control_recall and candidate_mrr > control_mrr:
        return True, "adopt"
    return False, "not_both_primary_improved"
