"""查询改写对照的预登记门槛与切片摘录。"""

from __future__ import annotations

from typing import Any

VALIDATION_TARGET = 0.90
EFFECTIVE_DATE_TARGET = 0.90
MAX_MAIN_RECALL_DROP = 0.01


def _recall_at_5(block: dict[str, Any] | None) -> float | None:
    if not isinstance(block, dict):
        return None
    recall = block.get("recall")
    if not isinstance(recall, dict):
        return None
    value = recall.get("@5")
    return float(value) if value is not None else None


def slice_metrics(report: dict[str, Any]) -> dict[str, float | None]:
    """从官方报告中取出决策用切片和主指标。"""
    return {
        "overall": _recall_at_5(report.get("overall")),
        "tuning": _recall_at_5((report.get("decision_inputs") or {}).get("summary")),
        "validation": _recall_at_5((report.get("by_split") or {}).get("validation")),
        "effective_date": _recall_at_5((report.get("by_category") or {}).get("effective_date")),
        "holdout": _recall_at_5((report.get("by_split") or {}).get("holdout")),
        "violations": int(
            ((report.get("decision_inputs") or {}).get("summary") or {}).get("safety", {}).get(
                "violation_cases",
                (report.get("overall") or {}).get("safety", {}).get("violation_cases", 0),
            )
            or 0
        ),
    }


def decide_query_rewrite(
    control_report: dict[str, Any],
    treatment_report: dict[str, Any],
) -> dict[str, Any]:
    """按切片门槛判断改写是否达到验收要求。holdout 不参与决定。"""
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

    if not slice_gains and "validation_recall@5_not_improved" not in failed:
        failed.append("triggered_slices_not_improved")

    if failed:
        return {
            "decision": "not_improved",
            "reason_code": "not_improved",
            "failed_gates": failed,
            "slice_gains": slice_gains,
            "slice_crossed_0_90": slice_crossed,
            "reason": "改写未在触发切片上把 Recall@5 提到 0.90 以上，或损害了主指标/安全切片",
        }
    return {
        "decision": "improved",
        "reason_code": "improved",
        "failed_gates": [],
        "slice_gains": slice_gains,
        "slice_crossed_0_90": slice_crossed,
        "reason": "改写提升了触发切片的 Recall@5，且未破坏越权切片与 overall/调参集",
    }
