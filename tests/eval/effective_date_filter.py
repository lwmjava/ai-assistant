"""生效日过滤对照的预登记门槛与切片摘录。

本模块只判断过滤开关这一单变量，不把查询改写纳入结论。
"""

from __future__ import annotations

from typing import Any

from tests.eval.query_rewrite import MAX_MAIN_RECALL_DROP, slice_metrics

VALIDATION_TARGET = 0.90
EFFECTIVE_DATE_TARGET = 0.90


def decide_effective_date_filter(
    control_report: dict[str, Any],
    treatment_report: dict[str, Any],
    *,
    rag031_expected_doc_hit: bool | None = None,
) -> dict[str, Any]:
    """判断打开生效日过滤后，是否按查询日召回了未来生效文档。holdout 不参与决定。"""
    control = slice_metrics(control_report)
    treatment = slice_metrics(treatment_report)
    failed: list[str] = []

    def _drop(name: str) -> None:
        before = control[name]
        after = treatment[name]
        if before is None or after is None:
            failed.append(f"{name}_missing")
            return
        if before - after > MAX_MAIN_RECALL_DROP:
            failed.append(f"{name}_recall@5_drop")

    _drop("overall")
    if control["tuning"] is not None or treatment["tuning"] is not None:
        _drop("tuning")

    if treatment["violations"] != 0 or treatment["violations"] > control["violations"]:
        failed.append("safety_violations")

    slice_gains: list[str] = []
    slice_crossed: list[str] = []
    for name, target in (
        ("validation", VALIDATION_TARGET),
        ("effective_date", EFFECTIVE_DATE_TARGET),
    ):
        before = control[name]
        after = treatment[name]
        if before is None or after is None:
            failed.append(f"{name}_missing")
            continue
        if after > before + 1e-12:
            slice_gains.append(name)
        if after + 1e-12 >= target:
            slice_crossed.append(name)
        elif after <= before + 1e-12:
            failed.append(f"{name}_recall@5_not_improved")

    if rag031_expected_doc_hit is False:
        failed.append("rag_031_future_doc_not_retrieved")
    elif rag031_expected_doc_hit is None:
        failed.append("rag_031_expected_doc_unknown")

    if not slice_gains and "validation_recall@5_not_improved" not in failed:
        failed.append("triggered_slices_not_improved")

    future_recalled = bool(rag031_expected_doc_hit) and "rag_031_future_doc_not_retrieved" not in failed
    if failed:
        return {
            "decision": "not_improved",
            "reason_code": "not_improved",
            "failed_gates": failed,
            "slice_gains": slice_gains,
            "slice_crossed_0_90": slice_crossed,
            "future_doc_recalled_by_query_date": future_recalled,
            "reason": "打开生效日过滤后，未能按查询日召回未来生效文档，或损害了主指标/安全切片",
        }
    return {
        "decision": "improved",
        "reason_code": "improved",
        "failed_gates": [],
        "slice_gains": slice_gains,
        "slice_crossed_0_90": slice_crossed,
        "future_doc_recalled_by_query_date": True,
        "reason": "打开生效日过滤后，按查询日召回了未来生效文档，触发切片 Recall@5 提升，且未破坏越权切片与 overall/调参集",
    }
