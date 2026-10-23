"""Independent CLI/workflow acceptance tests: synthetic E2, never model quality."""

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
import yaml

from scripts import run_agent_chain_eval as runner
from tests._utf8_subprocess import run_utf8

ROOT = Path(__file__).resolve().parents[2]


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject mappings GitHub would not be able to interpret uniquely."""


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def test_ci_workflow_has_unique_keys_and_one_agent_job():
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"),
        Loader=UniqueKeyLoader,
    )
    assert "agent-baseline" in workflow["jobs"]
    job = workflow["jobs"]["agent-baseline"]
    assert job["env"]["LLM_CAPABILITY_GUARD_ENABLED"] == "true"
    assert "needs" not in job
    assert "if" not in job
    assert workflow["permissions"] == {"contents": "read"}


MODEL = "deepseek-flash"
ENDPOINT = runner.endpoint_hash("https://api.deepseek.com/v1")


def independent_attempts(rows):
    def group(row, stage, key):
        return {"case_id": row["case_id"], "stage": stage,
                "attempts": [{"reply": row[key], "error_code": "",
                              "finish_reason": "unavailable", "native_tool_calls": None}]}
    return ([group(row, "route", "raw_reply") for row in rows]
            + [group(row, "tool_choice", "tool_reply") for row in rows if row["predicted_tools"]])


@pytest.fixture
def independent_report():
    """Construct contract data independently, without runner scorer/build_report."""
    cases = json.loads(runner.CASES_PATH.read_text(encoding="utf-8"))
    rows = []
    for case in cases:
        tools = case.get("expected_tools", [])
        code = (case["history"][1]["content"].split("```python\n")[1].split("```")[0]
                if case["case_id"] == "agent-004" else "print(1)")
        arguments = {"code": code}
        record = {
            "intent": "ok" if tools else "not_requested", "tool_name": "code_sandbox" if tools else "",
            "arguments_valid": bool(tools), "error_code": "",
            "arguments_hash": hashlib.sha256(json.dumps(arguments, sort_keys=True, ensure_ascii=False,
                                                        allow_nan=False).encode("utf-8")).hexdigest() if tools else "",
            "recording_attempts": 1 if tools else 0, "recorded_calls": 1 if tools else 0,
            "recording_result": "recorded" if tools else "not_attempted",
        }
        rows.append({
            "case_id": case["case_id"], "expected_route": case["expected_route"],
            "predicted_route": case["expected_route"],
            "critical_followup": case["critical_followup"], "passed": True,
            "raw_reply": "YES" if case["expected_route"] == "full" else "NO",
            "predicted_tools": tools,
            "tool_reply": (
                "<tool_call>" + json.dumps({"name": "code_sandbox", "arguments": arguments}) + "</tool_call>"
                if tools else ""
            ),
            "tool_record": record,
        })
    return {
        **runner.report_identity(), "report_schema_version": runner.REPORT_SCHEMA_VERSION,
        "gate_version": runner.GATE_VERSION, "provider": "openai-compatible",
        "model": MODEL, "endpoint_hash": ENDPOINT, "temperature": 0,
        "seed_supported": False, "git_commit": runner.git_commit(), "mode": "official",
        "model_requests": 12, "logical_requests": 12, "empty_response_retries": 0,
        "call_attempts": independent_attempts(rows), "route_accuracy": 1.0, "followup_recall": 1.0,
        "tool_selection_recall": 1.0,
        "tool_call_validity_recall": 1.0,
        "side_effects": {"sandbox": 0, "mcp": 0, "network": 0, "rag": 0, "database": 0},
        "side_effects_evidence": runner.SIDE_EFFECTS_EVIDENCE, "cases": rows,
    }


def invoke(script, path, *extra):
    # Child processes cannot use project credentials or reach a configured live provider.
    environment = dict(os.environ)
    environment.update(LLM_API_KEY="", LLM_INTENT_API_KEY="", LLM_FALLBACK_API_KEY="",
                       LLM_BASE_URL="http://127.0.0.1:1", LLM_PROVIDER="mock",
                       RAG_BACKEND="native", DATABASE_URL="sqlite:///./data/test_ai_assistant.db")
    result = run_utf8(
        [sys.executable, str(ROOT / "scripts" / script), "--report", str(path),
         "--expected-model", MODEL, "--expected-endpoint-hash", ENDPOINT, *extra],
        env=environment, check=False,
    )
    assert "Traceback" not in (result.stderr or "")
    return result


def save(path, report):
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


def reason_lines(result):
    # Importing existing RAG dependencies emits a fitz deprecation notice on stdout.
    # Protocol correctness is checked on all Agent lines, without weakening exit assertions.
    return [line for line in result.stdout.splitlines() if line.startswith("AGENT_")]


def test_independent_cli_accepts_current_complete_report(independent_report, tmp_path):
    result = invoke("agent_gate_check.py", save(tmp_path / "official.json", independent_report))
    assert result.returncode == 0
    assert reason_lines(result) == ["AGENT_GATE_PASS"]


@pytest.mark.parametrize("mutation", ["smoke", "history", "model", "endpoint", "raw-route",
                                     "raw-tool", "missing", "duplicate", "nan", "bool", "huge"])
def test_independent_cli_rejects_integrity_failures(independent_report, tmp_path, mutation):
    report = independent_report
    if mutation == "smoke":
        report["mode"] = "smoke"
    elif mutation == "history":
        report["report_schema_version"] = "historical"
    elif mutation == "model":
        report["model"] = "private-marker-do-not-echo"
    elif mutation == "endpoint":
        report["endpoint_hash"] = "0" * 64
    elif mutation == "raw-route":
        report["cases"][0]["raw_reply"] = "YES"
    elif mutation == "raw-tool":
        report["cases"][2]["tool_reply"] = "不调用工具"
    elif mutation == "missing":
        report["cases"].pop()
    elif mutation == "duplicate":
        report["cases"][1] = copy.deepcopy(report["cases"][0])
    else:
        report["route_accuracy"] = {"nan": float("nan"), "bool": True, "huge": 1e308}[mutation]
    result = invoke("agent_gate_check.py", save(tmp_path / "corrupt.json", report))
    assert result.returncode == 2
    assert reason_lines(result) == ["AGENT_GATE_ERROR:invalid_input_or_identity"]
    assert "private-marker" not in result.stdout + result.stderr


def corrupt_route(report, count, followup=False):
    selected = [row for row in report["cases"] if row["critical_followup"] == followup]
    for row in selected[:count]:
        row["predicted_route"] = "short" if row["expected_route"] == "full" else "full"
        row["raw_reply"] = "YES" if row["predicted_route"] == "full" else "NO"
        row["passed"] = False
    report["route_accuracy"] = (10 - count) / 10
    report["followup_recall"] = (2 - count) / 2 if followup else 1.0
    report["call_attempts"] = independent_attempts(report["cases"])


@pytest.mark.parametrize("kind,target", [("route", "route_accuracy"),
                                         ("followup", "followup_recall"),
                                         ("tool", "tool_selection_recall")])
def test_independent_cli_metric_failure_requires_exit_one(independent_report, tmp_path, kind, target):
    if kind == "tool":
        row = next(row for row in independent_report["cases"] if row["predicted_tools"])
        row["predicted_tools"] = []
        row["tool_reply"] = "不调用工具"
        group = next(group for group in independent_report["call_attempts"]
                     if group["case_id"] == row["case_id"] and group["stage"] == "tool_choice")
        group["attempts"][0]["reply"] = row["tool_reply"]
        independent_report["tool_selection_recall"] = 0.5
        row["tool_record"].update(intent="none", tool_name="", arguments_valid=False, error_code="no_tool_call",
                                 arguments_hash="", recording_attempts=0, recorded_calls=0,
                                 recording_result="not_attempted")
        independent_report["tool_call_validity_recall"] = 0.5
    else:
        corrupt_route(independent_report, 2 if kind == "route" else 1, kind == "followup")
    result = invoke("agent_gate_check.py", save(tmp_path / "fault.json", independent_report))
    assert result.returncode == 1
    expected = ["AGENT_GATE_METRIC:" + target]
    if kind == "tool":
        expected.append("AGENT_GATE_METRIC:tool_call_validity_recall")
    assert reason_lines(result) == expected


def test_independent_cli_boundary_and_probes(independent_report, tmp_path):
    corrupt_route(independent_report, 1)
    path = save(tmp_path / "boundary.json", independent_report)
    assert invoke("agent_gate_check.py", path).returncode == 0  # Exactly 0.9 is valid.
    out_dir = tmp_path / "synthetic-probes"
    result = invoke("agent_gate_probe.py", path, "--out-dir", str(out_dir))
    assert result.returncode == 0
    assert reason_lines(result) == ["AGENT_PROBE_PASS:route,followup,tool,arguments"]
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "not-model-quality" in manifest["evidence"]
    assert [row["exit_code"] for row in manifest["results"]] == [1, 1, 1, 1]
    original = (out_dir / "manifest.json").read_bytes()
    assert invoke("agent_gate_probe.py", path, "--out-dir", str(out_dir)).returncode == 2
    assert (out_dir / "manifest.json").read_bytes() == original


def test_independent_cli_probe_rejects_smoke_without_output(independent_report, tmp_path):
    independent_report["mode"] = "smoke"
    out_dir = tmp_path / "must-not-exist"
    result = invoke("agent_gate_probe.py", save(tmp_path / "smoke.json", independent_report),
                    "--out-dir", str(out_dir))
    assert result.returncode == 2
    assert not out_dir.exists()


def test_ci_secret_scope_and_fail_closed():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    assert "pull_request_target" not in workflow
    job = workflow["jobs"]["agent-baseline"]
    assert "LLM_API_KEY" not in job["env"]
    steps = job["steps"]
    secret_steps = [step for step in steps if "secrets.LLM_API_KEY" in str(step)]
    assert len(secret_steps) == 1
    assert "--mode official" in secret_steps[0]["run"]
    assert "if" not in secret_steps[0]
    assert "continue-on-error" not in job
    assert all("continue-on-error" not in step for step in steps)
    artifact = next(step for step in steps if "actions/upload-artifact" in step.get("uses", ""))
    assert artifact["with"]["path"] == "evals/reports/agent-ci/"


def invoke_runner(mode, out):
    environment = dict(os.environ)
    environment.update(LLM_API_KEY="", LLM_INTENT_API_KEY="", LLM_FALLBACK_API_KEY="",
                       LLM_BASE_URL="http://127.0.0.1:1", LLM_PROVIDER="mock",
                       LLM_INTENT_PROVIDER="mock", LLM_FALLBACK_PROVIDER="mock",
                       RAG_BACKEND="native", DATABASE_URL="sqlite:///./data/test_ai_assistant.db")
    return run_utf8(
        [sys.executable, str(ROOT / "scripts/run_agent_chain_eval.py"), "--mode", mode, "--out", str(out)],
        env=environment, check=False,
    )


def test_independent_runner_missing_real_identity_creates_no_report(tmp_path):
    out = tmp_path / "official.json"
    result = invoke_runner("official", out)
    assert result.returncode == 2
    assert "AGENT_EVAL_ERROR:real_single_uncached_intent_required" in result.stdout
    assert not out.exists()
    assert "Traceback" not in (result.stderr or "")


def test_independent_runner_smoke_and_existing_evidence(tmp_path):
    out = tmp_path / "smoke.json"
    result = invoke_runner("smoke", out)
    assert result.returncode == 0
    original = out.read_bytes()
    assert invoke("agent_gate_check.py", out).returncode == 2
    rerun = invoke_runner("smoke", out)
    assert rerun.returncode == 2
    assert "AGENT_EVAL_ERROR:report_exists" in rerun.stdout
    assert out.read_bytes() == original


@pytest.mark.parametrize("content", [b'\xff', b'{"mode":"official","mode":"smoke"}',
                                    b'{"secret-marker":NaN}', b'[' * 12 + b'0' + b']' * 12])
def test_independent_cli_hostile_json(content, tmp_path):
    out = tmp_path / "hostile.json"
    out.write_bytes(content)
    result = invoke("agent_gate_check.py", out)
    assert result.returncode == 2
    assert "secret-marker" not in result.stdout + result.stderr
