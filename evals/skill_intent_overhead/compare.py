"""Compare baseline and model-tier generation using deterministic mock models.

Mock results prove the measurement and selection wiring only. They are not a
real-model baseline and cannot be used to adopt or reject the experiment.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.agents.route import route_message
from app.llm.model_tier import select_generation_task

EVAL_VERSION = "skill-intent-overhead-0.1.0"
CASES_PATH = Path(__file__).with_name("cases.yaml")
STAGE_KEYS = ("skill_match", "route", "preflight", "retrieval", "tools", "answer")


@dataclass
class CaseResult:
    case_id: str
    query: str
    expected_route: str
    actual_route: str
    generation_task: str
    route_correct: bool
    skill_correct: bool
    tool_correct: bool
    first_token_ms: float | None
    first_model_token_ms: float | None
    duration_ms: float
    stage_durations_ms: dict[str, float] = field(default_factory=dict)
    llm_calls_by_purpose: dict[str, int] = field(default_factory=dict)
    llm_call_count: int = 0
    tool_call_count: int = 0
    eval_version: str = EVAL_VERSION

    @property
    def all_correct(self) -> bool:
        return self.route_correct and self.skill_correct and self.tool_correct


def load_cases(path: Path | None = None) -> list[dict[str, Any]]:
    payload = yaml.safe_load((path or CASES_PATH).read_text(encoding="utf-8"))
    if str(payload.get("version")) != EVAL_VERSION:
        raise ValueError("skill intent overhead case version mismatch")
    return list(payload["cases"])


def score_route_and_selection(query: str, expected: dict[str, Any]) -> dict[str, Any]:
    """Score deterministic route selection and the optional generation task."""
    route = route_message(query)
    expected_route = expected["expected_route"]
    expected_tools = set(expected.get("expected_tools") or [])
    generation_task = select_generation_task(route)
    return {
        "actual_route": route.kind.value,
        "generation_task": generation_task,
        "route_correct": route.kind.value == expected_route,
        "tool_correct": set(route.tool_names) == expected_tools,
        "skill_correct": expected.get("expected_skill") is None,
        "expected_route": expected_route,
    }


def summarize_group(rows: list[CaseResult], label: str, source: str) -> dict[str, Any]:
    first_tokens = [row.first_model_token_ms for row in rows if row.first_model_token_ms is not None]
    if not first_tokens:
        first_tokens = [row.first_token_ms for row in rows if row.first_token_ms is not None]
    durations = [row.duration_ms for row in rows]
    stage_means = {
        key: round(statistics.mean(row.stage_durations_ms.get(key, 0.0) for row in rows), 4)
        for key in STAGE_KEYS
    }
    purpose_means = {}
    purposes = {key for row in rows for key in row.llm_calls_by_purpose}
    for purpose in sorted(purposes):
        purpose_means[purpose] = round(
            statistics.mean(row.llm_calls_by_purpose.get(purpose, 0) for row in rows), 4
        )
    return {
        "label": label,
        "source": source,
        "eval_version": EVAL_VERSION,
        "n": len(rows),
        "route_accuracy": round(sum(row.route_correct for row in rows) / len(rows), 4) if rows else 0.0,
        "skill_accuracy": round(sum(row.skill_correct for row in rows) / len(rows), 4) if rows else 0.0,
        "tool_accuracy": round(sum(row.tool_correct for row in rows) / len(rows), 4) if rows else 0.0,
        "mean_duration_ms": round(statistics.mean(durations), 4) if durations else None,
        "p95_first_token_ms": round(_percentile(first_tokens, 0.95), 4) if first_tokens else None,
        "mean_first_token_ms": round(statistics.mean(first_tokens), 4) if first_tokens else None,
        "mean_llm_call_count": round(statistics.mean(row.llm_call_count for row in rows), 4) if rows else 0.0,
        "mean_stage_durations_ms": stage_means,
        "mean_llm_calls_by_purpose": purpose_means,
    }


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def compare_reports(baseline: dict[str, Any], treatment: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Compare one treatment against baseline. Mock source cannot be adopted."""
    def _delta(key: str) -> float | None:
        left = baseline.get(key)
        right = treatment.get(key)
        if left is None or right is None:
            return None
        return round(right - left, 4)

    quality_ok = all(
        treatment.get(key, 0) >= baseline.get(key, 0)
        for key in ("route_accuracy", "skill_accuracy", "tool_accuracy")
    )
    first_token_delta = _delta("p95_first_token_ms")
    latency_improved = first_token_delta is not None and baseline.get("p95_first_token_ms") not in {None, 0} and (
        first_token_delta <= -0.15 * float(baseline["p95_first_token_ms"])
    )
    if source != "real":
        decision = "not_a_real_baseline"
        reason = "mock results cannot be used to adopt or reject the experiment"
    elif not quality_ok:
        decision = "not_adopted"
        reason = "routing, skill, or tool accuracy did not hold"
    elif latency_improved:
        decision = "adopt_candidate"
        reason = "real p95 first-token latency improved by at least 15% without quality loss"
    else:
        decision = "not_adopted"
        reason = "no qualifying real-model latency or cost improvement"
    return {
        "source": source,
        "eval_version": EVAL_VERSION,
        "decision": decision,
        "reason": reason,
        "quality_held": quality_ok,
        "p95_first_token_delta_ms": first_token_delta,
        "route_accuracy_delta": _delta("route_accuracy"),
        "skill_accuracy_delta": _delta("skill_accuracy"),
        "tool_accuracy_delta": _delta("tool_accuracy"),
        "real_model_run": source == "real",
    }


def case_result_from_trace(
    case: dict[str, Any],
    scored: dict[str, Any],
    summary: dict[str, Any],
) -> CaseResult:
    return CaseResult(
        case_id=str(case["id"]),
        query=str(case["query"]),
        expected_route=scored["expected_route"],
        actual_route=scored["actual_route"],
        generation_task=scored["generation_task"],
        route_correct=scored["route_correct"],
        skill_correct=scored["skill_correct"],
        tool_correct=scored["tool_correct"],
        first_token_ms=summary.get("first_token_ms"),
        first_model_token_ms=summary.get("first_model_token_ms"),
        duration_ms=float(summary.get("duration_ms") or 0.0),
        stage_durations_ms=dict(summary.get("stage_durations_ms") or {}),
        llm_calls_by_purpose=dict(summary.get("llm_calls_by_purpose") or {}),
        llm_call_count=int(summary.get("llm_call_count") or 0),
        tool_call_count=int(summary.get("tool_call_count") or 0),
        eval_version=str(summary.get("eval_version") or EVAL_VERSION),
    )
