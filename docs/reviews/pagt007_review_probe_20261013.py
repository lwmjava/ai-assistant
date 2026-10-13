"""Independent CLI counterexamples: synthetic E2 mechanics, no model requests."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import run_agent_chain_eval as runner  # noqa: E402

MODEL = "synthetic-review-real-model"
ENDPOINT = runner.endpoint_hash("https://isolated.invalid/v1")
RESULTS = []


def recompute(report):
    cases = runner.load_cases()
    correct, follow, tools = [], [], []
    for case, row in zip(cases, report["cases"], strict=True):
        route = "full" if runner.preflight_needs_full_pipeline(row["raw_reply"]) else "short"
        row["predicted_route"] = route
        row["passed"] = route == case["expected_route"]
        parsed = runner.inspect_tool_call(row["tool_reply"])
        names = [parsed.call.name] if parsed.status == "ok" and parsed.call else []
        row["predicted_tools"] = names
        correct.append(row["passed"])
        if case["critical_followup"]:
            follow.append(row["passed"])
        if case.get("expected_tools"):
            tools.append(any(name in case["expected_tools"] for name in names))
    report.update(route_accuracy=sum(correct) / len(correct),
                  followup_recall=sum(follow) / len(follow),
                  tool_selection_recall=sum(tools) / len(tools))


def cli(script, path, extra=()):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script),
                           "--report", str(path), "--expected-model", MODEL,
                           "--expected-endpoint-hash", ENDPOINT, *extra],
                          capture_output=True, text=True, timeout=60, check=False)


def record(name, result, code, reason):
    assert result.returncode == code, (name, result.returncode, result.stdout)
    assert reason in result.stdout.splitlines(), (name, result.stdout)
    assert "Traceback" not in result.stdout + result.stderr, name
    RESULTS.append({"case": name, "exit": result.returncode, "reason": reason})


def main():
    cases = runner.load_cases()
    routes = [case["expected_route"] for case in cases]
    raw = ["YES" if route == "full" else "NO" for route in routes]
    names = [case.get("expected_tools", []) for case in cases]
    toolraw = ['<tool_call>{"name":"code_sandbox","arguments":{"code":"print(1)"}}</tool_call>'
               if name else "" for name in names]
    provider = runner.OpenAICompatibleProvider("https://isolated.invalid/v1", "isolated-review-key", MODEL)
    base = runner.build_report("official", provider, cases, routes, raw, names, toolraw)
    error = "AGENT_GATE_ERROR:invalid_input_or_identity"
    with tempfile.TemporaryDirectory(prefix="pagt007-independent-") as directory:
        work = Path(directory)
        for relative, expected in [("evals/datasets/agent-chain-v0.1/cases.json", runner.FROZEN_CASES_HASH),
                                   ("evals/datasets/agent-chain-v0.1/index.json", runner.FROZEN_INDEX_HASH)]:
            blob = subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT)
            assert b"\r\n" not in blob
            assert hashlib.sha256(blob).hexdigest() == expected
            for kind, data in [("lf", blob), ("crlf", blob.replace(b"\n", b"\r\n"))]:
                newline_path = work / (Path(relative).name + kind)
                newline_path.write_bytes(data)
                assert runner.content_hash(newline_path) == expected
                RESULTS.append({"case": Path(relative).name + "-" + kind,
                                "exit": 0, "reason": "frozen-canonical-hash-matches-git-blob"})
            newline_path.write_bytes(blob + b" ")
            assert runner.content_hash(newline_path) != expected
            RESULTS.append({"case": Path(relative).name + "-nonnewline-drift",
                            "exit": 0, "reason": "content-drift-still-changes-hash"})

        def check(name, payload, code=2, reason=error):
            path = work / (name + ".json")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            record(name, cli("agent_gate_check.py", path), code, reason)
            return path

        path = check("valid-synthetic-structure", base, 0, "AGENT_GATE_PASS")
        for field, value in [("mode", "smoke"), ("provider", "fallback"),
                             ("model", "other-model"), ("endpoint_hash", "a" * 64),
                             ("git_commit", "a" * 40), ("prompt_hash", "a" * 64),
                             ("source_hashes", {}), ("dataset_hash", "a" * 64),
                             ("data_hash_algorithm", "different-hash-algorithm"),
                             ("model_requests", True), ("temperature", True),
                             ("route_accuracy", True), ("route_accuracy", float("nan")),
                             ("route_accuracy", float("inf")), ("route_accuracy", -0.1)]:
            bad = copy.deepcopy(base)
            bad[field] = value
            check("bad-" + field + "-" + str(len(RESULTS)), bad)
        bad = copy.deepcopy(base)
        bad["cases"][0]["raw_reply"] = "YES"
        check("raw-route-self-consistency", bad)
        bad = copy.deepcopy(base)
        bad["cases"][2]["tool_reply"] = "不调用工具"
        check("raw-tool-self-consistency", bad)
        for name, indexes, metric in [("route", [0, 1], "route_accuracy"),
                                      ("followup", [2], "followup_recall")]:
            bad = copy.deepcopy(base)
            for index in indexes:
                bad["cases"][index]["raw_reply"] = "NO" if routes[index] == "full" else "YES"
            recompute(bad)
            check("metric-" + name, bad, 1, "AGENT_GATE_METRIC:" + metric)
        bad = copy.deepcopy(base)
        bad["cases"][2]["tool_reply"] = "不调用工具"
        recompute(bad)
        check("metric-tool", bad, 1, "AGENT_GATE_METRIC:tool_selection_recall")
        threshold = copy.deepcopy(base)
        threshold["cases"][0]["raw_reply"] = "YES"
        recompute(threshold)
        boundary_path = check("boundary-route-0.9", threshold, 0, "AGENT_GATE_PASS")
        for label, source in [("probe-perfect", path), ("probe-0.9-base", boundary_path)]:
            out = work / label
            record(label, cli("agent_gate_probe.py", source, ["--out-dir", str(out)]),
                   0, "AGENT_PROBE_PASS:route,followup,tool")
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            assert [row["target_metric"] for row in manifest["results"]] == [
                "route_accuracy", "followup_recall", "tool_selection_recall"]
            record(label + "-repeat", cli("agent_gate_probe.py", source, ["--out-dir", str(out)]),
                   2, "AGENT_PROBE_ERROR:invalid_report_or_probe")
        smoke = copy.deepcopy(base)
        smoke["mode"] = "smoke"
        smoke_path = check("smoke-rejected", smoke)
        out = work / "invalid-base"
        record("probe-invalid-base", cli("agent_gate_probe.py", smoke_path, ["--out-dir", str(out)]),
               2, "AGENT_PROBE_ERROR:invalid_report_or_probe")
        assert not out.exists()
        for label, body in [("duplicate-json", b'{"mode":"smoke","mode":"official"}'),
                            ("non-utf8", b'\xff'), ("deep-json", b'{"x":' + b'[' * 12 + b'0' + b']' * 12 + b'}'),
                            ("over-limit", b' ' * (runner.MAX_REPORT_BYTES + 1))]:
            bad_path = work / (label + ".json")
            bad_path.write_bytes(body)
            record(label, cli("agent_gate_check.py", bad_path), 2, error)
    paths = ["scripts/run_agent_chain_eval.py", "scripts/agent_gate_check.py",
             "scripts/agent_gate_probe.py", ".github/workflows/ci.yml",
             "tests/eval/test_agent_gate.py", "tests/eval/test_agent_eval_runner.py",
             "tests/eval/test_agent_gate_entry.py"]
    print(json.dumps({"identity": "/root/pagt007_counterexample_review_v2",
                      "evidence": "E2 synthetic CLI mechanics, not model quality or GitHub CI",
                      "model_requests": 0, "results": RESULTS,
                      "hashes": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                                 for path in paths}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
