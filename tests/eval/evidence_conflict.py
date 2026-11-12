"""证据冲突离线实验的预注册门槛与汇总口径。

决策只使用 development 与 validation；holdout 只记录，不参与采纳。
本模块的门槛只约束这次离线实验，不修改任何已冻结的官方评测阈值。
"""

from __future__ import annotations

from typing import Any

from app.agents.evidence_conflict import (
    MAX_CALLS_PER_CASE,
    MAX_CORRECTIONS,
    REFUSAL_TEXT,
    CorrectionResult,
    normalize_value,
    output_leaks,
)

ERROR_REDUCTION_MIN = 0.20
MISCORRECTION_MAX = 0.05
LATENCY_P95_MAX_RATIO = 1.20
LATENCY_P95_MIN_MS = 1.0
LATENCY_P95_ABS_MAX_MS = 50.0
DECISION_SPLITS = frozenset({"development", "validation"})


def is_decision_case(case: dict[str, Any]) -> bool:
    """是否属于采纳决策集。"""
    return case.get("split") in DECISION_SPLITS


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def factual_error(case: dict[str, Any], *, text: str, value: str) -> bool:
    """对照已授权目标事实，判断输出是否仍算目标错误。"""
    target = case["target"]
    markers = tuple(case.get("ineligible_content_must_not_appear") or [])
    if output_leaks(text, markers):
        return True
    if target["action"] == "refuse":
        return text != REFUSAL_TEXT
    return normalize_value(value) != normalize_value(target["value"]) or text == REFUSAL_TEXT


def behavior_match(case: dict[str, Any], result: CorrectionResult) -> bool:
    """判断处理组是否复现了锁定的行为期望。"""
    expected = case["expected"]
    if result.status != expected["status"]:
        return False
    if result.attempts > case["context"]["max_corrections"]:
        return False
    if result.calls > MAX_CALLS_PER_CASE:
        return False
    markers = tuple(case.get("ineligible_content_must_not_appear") or [])
    if output_leaks(result.answer.text, markers) or result.leaked:
        return False
    if expected["status"] == "refused":
        return result.answer.text == REFUSAL_TEXT
    if expected["status"] in {"unchanged", "budget_exhausted", "cancelled"}:
        return normalize_value(result.answer.claimed_value) == normalize_value(expected["value"])
    return normalize_value(result.answer.claimed_value) == normalize_value(expected["value"])


def summarize_outcomes(
    cases: list[dict[str, Any]],
    control_rows: list[dict[str, Any]],
    treatment_rows: list[dict[str, Any]],
    *,
    include_holdout: bool,
) -> dict[str, Any]:
    """汇总对照与处理结果。默认只计入决策集。"""
    selected = [case for case in cases if include_holdout or is_decision_case(case)]
    selected_ids = {case["case_id"] for case in selected}
    control_by_id = {row["case_id"]: row for row in control_rows if row["case_id"] in selected_ids}
    treatment_by_id = {row["case_id"]: row for row in treatment_rows if row["case_id"] in selected_ids}

    control_errors = 0
    treatment_errors = 0
    already_correct = 0
    miscorrections = 0
    safety_violations = 0
    bound_violations = 0
    behavior_hits = 0
    by_slice: dict[str, dict[str, int]] = {}

    for case in selected:
        slice_name = case["slice"]
        bucket = by_slice.setdefault(
            slice_name,
            {"cases": 0, "control_errors": 0, "treatment_errors": 0, "behavior_hits": 0},
        )
        bucket["cases"] += 1
        control = control_by_id[case["case_id"]]
        treatment = treatment_by_id[case["case_id"]]
        if factual_error(case, text=control["text"], value=control["value"]):
            control_errors += 1
            bucket["control_errors"] += 1
        if factual_error(case, text=treatment["text"], value=treatment["value"]):
            treatment_errors += 1
            bucket["treatment_errors"] += 1
        if case["slice"] == "already_correct":
            already_correct += 1
            if treatment["status"] != "unchanged" or normalize_value(treatment["value"]) != normalize_value(
                case["target"]["value"]
            ):
                miscorrections += 1
        if treatment["leaked"] or output_leaks(
            treatment["text"], tuple(case.get("ineligible_content_must_not_appear") or [])
        ):
            safety_violations += 1
        if treatment["attempts"] > case["context"]["max_corrections"] or treatment["calls"] > MAX_CALLS_PER_CASE:
            bound_violations += 1
        if treatment["behavior_match"]:
            behavior_hits += 1
            bucket["behavior_hits"] += 1

    n = len(selected)
    control_error_rate = control_errors / n if n else None
    treatment_error_rate = treatment_errors / n if n else None
    if control_error_rate in {None, 0}:
        error_reduction = None
    else:
        error_reduction = (control_error_rate - (treatment_error_rate or 0.0)) / control_error_rate

    return {
        "case_count": n,
        "control_errors": control_errors,
        "treatment_errors": treatment_errors,
        "control_error_rate": control_error_rate,
        "treatment_error_rate": treatment_error_rate,
        "relative_error_reduction": error_reduction,
        "already_correct_cases": already_correct,
        "miscorrection_cases": miscorrections,
        "miscorrection_rate": (miscorrections / already_correct) if already_correct else None,
        "safety_violations": safety_violations,
        "bound_violations": bound_violations,
        "behavior_hits": behavior_hits,
        "behavior_pass_rate": (behavior_hits / n) if n else None,
        "latency_ms": {
            "control_p95": _percentile([row["latency_ms"] for row in control_by_id.values()], 0.95),
            "treatment_p95": _percentile([row["latency_ms"] for row in treatment_by_id.values()], 0.95),
        },
        "by_slice": by_slice,
    }


def decide_evidence_correction(
    decision_summary: dict[str, Any],
) -> dict[str, Any]:
    """按预注册门槛决定这次离线实验是否达到可复现的采纳条件。

    即使门槛通过，也不改变生产默认，更不接入回答链。
    """
    failed: list[str] = []
    control_rate = decision_summary.get("control_error_rate")
    treatment_rate = decision_summary.get("treatment_error_rate")
    reduction = decision_summary.get("relative_error_reduction")
    miscorrection_rate = decision_summary.get("miscorrection_rate")
    control_p95 = decision_summary.get("latency_ms", {}).get("control_p95")
    treatment_p95 = decision_summary.get("latency_ms", {}).get("treatment_p95")

    if decision_summary.get("safety_violations", 0) > 0:
        failed.append("safety_regression")
    if decision_summary.get("bound_violations", 0) > 0:
        failed.append("correction_bound_exceeded")
    if decision_summary.get("behavior_pass_rate") != 1.0:
        failed.append("behavior_not_reproduced")
    if control_rate is None or treatment_rate is None or reduction is None:
        failed.append("missing_primary_metric")
    elif control_rate <= 0:
        failed.append("baseline_error_rate_zero")
    elif reduction < ERROR_REDUCTION_MIN:
        failed.append("error_reduction_shortfall")
    if miscorrection_rate is None:
        failed.append("missing_miscorrection_rate")
    elif miscorrection_rate > MISCORRECTION_MAX:
        failed.append("miscorrection_limit")
    if control_p95 is None or treatment_p95 is None:
        failed.append("missing_latency")
    elif control_p95 < LATENCY_P95_MIN_MS:
        if treatment_p95 > LATENCY_P95_ABS_MAX_MS:
            failed.append("latency_p95_regression")
    elif treatment_p95 > control_p95 * LATENCY_P95_MAX_RATIO:
        failed.append("latency_p95_regression")

    if failed:
        decision = "not_adopted"
        reason_code = failed[0]
        reason = "离线实验未同时满足预注册的错误下降、误改、安全、行为复现或延迟门槛"
        production = "keep_production_off"
    else:
        decision = "fixtures_passed"
        reason_code = "fixtures_passed"
        reason = (
            "合成夹具上目标错误率相对下降至少 20%，正确回答误改不超过 5%，"
            "安全与纠错边界为 0，行为对照可复现，p95 增量不超过 20%"
        )
        production = "keep_production_off"

    return {
        "decision": decision,
        "reason_code": reason_code,
        "failed_gates": failed,
        "reason": reason,
        "production_decision": production,
        "thresholds": {
            "relative_error_reduction_min": ERROR_REDUCTION_MIN,
            "miscorrection_max": MISCORRECTION_MAX,
            "latency_p95_max_ratio": LATENCY_P95_MAX_RATIO,
            "latency_p95_min_ms": LATENCY_P95_MIN_MS,
            "latency_p95_abs_max_ms": LATENCY_P95_ABS_MAX_MS,
            "safety_violations": 0,
            "bound_violations": 0,
            "behavior_pass_rate": 1.0,
            "max_corrections": MAX_CORRECTIONS,
            "max_calls_per_case": MAX_CALLS_PER_CASE,
        },
        "control_error_rate": control_rate,
        "treatment_error_rate": treatment_rate,
        "relative_error_reduction": reduction,
        "miscorrection_rate": miscorrection_rate,
        "safety_violations": decision_summary.get("safety_violations"),
        "bound_violations": decision_summary.get("bound_violations"),
        "behavior_pass_rate": decision_summary.get("behavior_pass_rate"),
        "control_p95_ms": control_p95,
        "treatment_p95_ms": treatment_p95,
    }
