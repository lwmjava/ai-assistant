"""Strict official Agent report gate; predictions are rebuilt from raw replies."""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import run_agent_chain_eval as evaluator  # noqa: E402

THRESHOLDS = {"route_accuracy": 0.9, "followup_recall": 1.0, "tool_selection_recall": 1.0}
MAX_DEPTH = 8
FROZEN_CASES_HASH = "5bfad42d3a2d765363d3489036ea1b91346c6fff49ca58d13fe9147ff974905b"
FROZEN_INDEX_HASH = "8cc903dbc9f406bc070bea620952396a2323e3b41a8eb44ca0a72044bc57669d"


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def _depth(value: object, level: int = 0) -> None:
    _require(level <= MAX_DEPTH, "report_depth")
    if isinstance(value, dict):
        for child in value.values():
            _depth(child, level + 1)
    elif isinstance(value, list):
        for child in value:
            _depth(child, level + 1)


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        _require(key not in result, "duplicate_key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("nonfinite_json")


def read_report(path: Path) -> dict:
    with path.open("rb") as handle:
        blob = handle.read(evaluator.MAX_REPORT_BYTES + 1)
    _require(len(blob) <= evaluator.MAX_REPORT_BYTES, "report_size")
    try:
        result = json.loads(blob.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid_json") from exc
    _require(isinstance(result, dict), "report_object")
    _depth(result)
    return result


def _metric(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 1


def _reply(value: object) -> str:
    _require(isinstance(value, str), "reply_type")
    _require(len(value.encode("utf-8")) <= evaluator.MAX_REPLY_BYTES, "reply_size")
    return value


def check_report(report: dict, expected_model: str, expected_endpoint_hash: str) -> list[str]:
    _require(isinstance(report, dict), "report_object")
    _depth(report)
    _require(isinstance(expected_model, str) and bool(expected_model.strip())
             and expected_model.lower() != "unknown"
             and not any(token in expected_model.lower() for token in ("mock", "smoke", "fallback")),
             "expected_model")
    _require(isinstance(expected_endpoint_hash, str)
             and re.fullmatch(r"[0-9a-f]{64}", expected_endpoint_hash) is not None, "expected_endpoint")
    _require(report.get("mode") == "official", "official_required")
    _require(report.get("provider") == "openai-compatible", "real_provider_required")
    _require(report.get("model") == expected_model, "model_identity")
    _require(report.get("endpoint_hash") == expected_endpoint_hash, "endpoint_identity")
    _require(report.get("report_schema_version") == evaluator.REPORT_SCHEMA_VERSION, "report_version")
    _require(report.get("gate_version") == evaluator.GATE_VERSION, "gate_version")
    _require(type(report.get("temperature")) in {int, float} and report["temperature"] == 0,
             "temperature")
    _require(report.get("seed_supported") is False, "seed_identity")
    _require(type(report.get("model_requests")) is int and report["model_requests"] == 12,
             "request_count")
    _require(isinstance(report.get("git_commit"), str)
             and re.fullmatch(r"[0-9a-f]{40}", report["git_commit"]) is not None
             and report["git_commit"] == evaluator.git_commit(), "git_identity")
    _require(report.get("side_effects_evidence") == "selection-only-design-not-runtime-counters",
             "side_effects_evidence")
    effects = report.get("side_effects")
    _require(isinstance(effects, dict) and effects.keys() == evaluator.SIDE_EFFECTS.keys()
             and all(type(value) is int and value == 0 for value in effects.values()), "side_effects")
    cases = evaluator.load_cases()
    index = json.loads(evaluator.INDEX_PATH.read_text(encoding="utf-8"))
    _require(evaluator.dataset_hash() == FROZEN_CASES_HASH
             and evaluator.content_hash(evaluator.INDEX_PATH) == FROZEN_INDEX_HASH
             and index.get("thresholds") == THRESHOLDS and len(cases) == 10
             and sum(bool(case["critical_followup"]) for case in cases) == 2
             and sum(bool(case.get("expected_tools")) for case in cases) == 2, "frozen_contract")
    for key, value in evaluator.report_identity(cases).items():
        _require(report.get(key) == value, "binding_" + key)
    rows = report.get("cases")
    _require(isinstance(rows, list) and len(rows) == len(cases), "case_coverage")
    routes: list[str] = []
    chosen: list[list[str]] = []
    for case, row in zip(cases, rows, strict=True):
        _require(isinstance(row, dict), "case_object")
        _require(row.get("case_id") == case["case_id"], "case_identity")
        _require(row.get("expected_route") == case["expected_route"], "case_expected")
        _require(type(row.get("critical_followup")) is bool
                 and row["critical_followup"] == case["critical_followup"], "case_followup")
        route = "full" if evaluator.preflight_needs_full_pipeline(_reply(row.get("raw_reply"))) else "short"
        _require(row.get("predicted_route") == route, "raw_route_binding")
        _require(type(row.get("passed")) is bool
                 and row["passed"] == (route == case["expected_route"]), "case_passed")
        raw_tool = _reply(row.get("tool_reply"))
        if not case.get("expected_tools"):
            _require(raw_tool == "", "unexpected_tool_reply")
            names = []
        else:
            intent = evaluator.inspect_tool_call(raw_tool)
            names = [intent.call.name] if intent.status == "ok" and intent.call is not None else []
        _require(row.get("predicted_tools") == names, "raw_tool_binding")
        routes.append(route)
        chosen.append(names)
    # Independent counts: a broken evaluator scorer must never corroborate itself.
    correct = [route == case["expected_route"] for case, route in zip(cases, routes, strict=True)]
    followups = [ok for case, ok in zip(cases, correct, strict=True) if case["critical_followup"]]
    tool_hits = [any(name in case["expected_tools"] for name in names)
                 for case, names in zip(cases, chosen, strict=True) if case.get("expected_tools")]
    scores = {"route_accuracy": sum(correct) / len(cases),
              "followup_recall": sum(followups) / len(followups),
              "tool_selection_recall": sum(tool_hits) / len(tool_hits)}
    for name in THRESHOLDS:
        _require(_metric(report.get(name)), "metric_number")
        _require(report[name] == scores[name], "metric_integrity")
    return [name for name, threshold in THRESHOLDS.items() if scores[name] < threshold]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-model", required=True)
    parser.add_argument("--expected-endpoint-hash", required=True)
    args = parser.parse_args()
    try:
        failed = check_report(read_report(args.report), args.expected_model, args.expected_endpoint_hash)
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- never echo untrusted report or credential content
        print("AGENT_GATE_ERROR:invalid_input_or_identity")
        raise SystemExit(2) from None
    for name in failed:
        print("AGENT_GATE_METRIC:" + name)
    if not failed:
        print("AGENT_GATE_PASS")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
