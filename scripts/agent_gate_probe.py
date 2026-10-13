"""Controlled report faults prove gate mechanics, never model quality."""
from __future__ import annotations

import argparse
import copy
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import agent_gate_check as gate  # noqa: E402
from scripts import run_agent_chain_eval as evaluator  # noqa: E402


def recompute(report: dict) -> None:
    cases = evaluator.load_cases()
    routes = []
    chosen = []
    records = []
    for case, row in zip(cases, report["cases"], strict=True):
        route = "full" if evaluator.preflight_needs_full_pipeline(row["raw_reply"]) else "short"
        row["predicted_route"] = route
        row["passed"] = route == case["expected_route"]
        intent = evaluator.inspect_tool_call(row["tool_reply"])
        names = [intent.call.name] if intent.status == "ok" and intent.call is not None else []
        row["predicted_tools"] = names
        routes.append(route)
        chosen.append(names)
        row["tool_record"] = evaluator.expected_tool_record(case, row["tool_reply"])
        records.append(row["tool_record"])
    report.update({key: value for key, value in evaluator.score(cases, routes).items() if key != "rows"})
    report["tool_selection_recall"] = evaluator.score_tools(cases, chosen)["tool_selection_recall"]
    report["tool_call_validity_recall"] = evaluator.score_valid_calls(cases, records)


def run_probes(report: dict, expected_model: str, expected_endpoint_hash: str, out_dir: Path) -> dict:
    if not evaluator.allowed_report_path(out_dir):
        raise ValueError("report_path_out_of_scope")
    if gate.check_report(report, expected_model, expected_endpoint_hash):
        raise ValueError("base_report_failed")
    # Refuse existing directory to preserve earlier probe evidence, including partial failures.
    out_dir.mkdir(parents=True, exist_ok=False)
    results = []
    targets = {"route": "route_accuracy", "followup": "followup_recall", "tool": "tool_selection_recall",
               "arguments": "tool_call_validity_recall"}
    cases = evaluator.load_cases()
    for kind, metric in targets.items():
        mutant = copy.deepcopy(report)
        if kind in {"tool", "arguments"}:
            index = next(i for i, case in enumerate(cases) if case.get("expected_tools"))
            mutant["cases"][index]["tool_reply"] = (
                "不调用工具" if kind == "tool" else
                '<tool_call>{"name":"code_sandbox","arguments":{}}</tool_call>'
            )
        else:
            indexes = [i for i, case in enumerate(cases)
                       if bool(case["critical_followup"]) == (kind == "followup")
                       and mutant["cases"][i]["passed"]]
            for index in indexes[:1 if kind == "followup" else 2]:
                row = mutant["cases"][index]
                row["raw_reply"] = "NO" if row["predicted_route"] == "full" else "YES"
        recompute(mutant)
        path = out_dir / f"synthetic-{kind}.json"
        evaluator.write_report(path, mutant)
        result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts/agent_gate_check.py"),
                                 "--report", str(path), "--expected-model", expected_model,
                                 "--expected-endpoint-hash", expected_endpoint_hash],
                                capture_output=True, text=True, timeout=60, check=False)
        expected_reason = "AGENT_GATE_METRIC:" + metric
        reasons = result.stdout.splitlines()
        if result.returncode != 1 or expected_reason not in reasons or any(
                line.startswith("AGENT_GATE_ERROR:") for line in reasons):
            raise ValueError("probe_did_not_fail_for_target_metric")
        results.append({"probe": kind, "exit_code": result.returncode, "target_metric": metric,
                        "reason_codes": [line for line in reasons if line.startswith("AGENT_GATE_METRIC:")]})
    manifest = {"evidence": "synthetic-controlled-faults-mechanism-only-not-model-quality",
                "source_model": expected_model, "results": results}
    evaluator.write_report(out_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-model", required=True)
    parser.add_argument("--expected-endpoint-hash", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        run_probes(gate.read_report(args.report), args.expected_model, args.expected_endpoint_hash, args.out_dir)
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- safe classification only
        print("AGENT_PROBE_ERROR:invalid_report_or_probe")
        raise SystemExit(2) from None
    print("AGENT_PROBE_PASS:route,followup,tool,arguments")


if __name__ == "__main__":
    main()
