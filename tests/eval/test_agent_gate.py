"""Synthetic reports exercise gate mechanics; these are not official quality evidence."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.llm.openai_compatible import OpenAICompatibleProvider
from scripts import agent_gate_check as gate
from scripts import agent_gate_probe as probe
from scripts import run_agent_chain_eval as evaluator

MODEL = "deepseek-chat"
ENDPOINT = evaluator.endpoint_hash("https://api.deepseek.com/v1")


@pytest.fixture
def report():
    cases = evaluator.load_cases()
    routes = [case["expected_route"] for case in cases]
    raws = ["YES" if route == "full" else "NO" for route in routes]
    names = [case.get("expected_tools") or [] for case in cases]
    tools = ['<tool_call>{"name":"code_sandbox","arguments":{"code":"print(1)"}}</tool_call>'
             if name else "" for name in names]
    provider = OpenAICompatibleProvider(base_url="https://api.deepseek.com/v1", api_key="synthetic-test", model=MODEL)
    return evaluator.build_report("official", provider, cases, routes, raws, names, tools)


def cli(path: Path):
    return subprocess.run([sys.executable, str(evaluator.REPO_ROOT / "scripts/agent_gate_check.py"),
                           "--report", str(path), "--expected-model", MODEL,
                           "--expected-endpoint-hash", ENDPOINT], capture_output=True, text=True, timeout=60)


def test_gate_valid_official_structure_and_cli(report, tmp_path):
    assert gate.check_report(report, MODEL, ENDPOINT) == []
    path = tmp_path / "report.json"
    evaluator.write_report(path, report)
    result = cli(path)
    assert result.returncode == 0
    assert "AGENT_GATE_PASS" in result.stdout.splitlines()


@pytest.mark.parametrize("field,value", [
    ("mode", "smoke"), ("model", "different"), ("endpoint_hash", "a" * 64),
    ("provider", "fallback"), ("prompt_hash", "a" * 64), ("dataset_hash", "a" * 64),
    ("source_hashes", {}), ("model_requests", True), ("temperature", True),
    ("route_accuracy", float("nan")), ("route_accuracy", float("inf")),
    ("route_accuracy", -1), ("route_accuracy", 2), ("route_accuracy", True),
    ("route_accuracy", 0.9), ("side_effects", {"database": 0}),
])
def test_gate_rejects_binding_and_numerical_corruption(report, field, value):
    report[field] = value
    with pytest.raises(ValueError):
        gate.check_report(report, MODEL, ENDPOINT)


@pytest.mark.parametrize("mutation", ["raw", "tools", "duplicate", "missing", "expected", "passed", "oversize"])
def test_gate_rejects_case_corruption(report, mutation):
    rows = report["cases"]
    if mutation == "raw":
        rows[0]["raw_reply"] = "YES" if rows[0]["predicted_route"] == "short" else "NO"
    elif mutation == "tools":
        rows[2]["tool_reply"] = "不调用工具"
    elif mutation == "duplicate":
        rows[1] = copy.deepcopy(rows[0])
    elif mutation == "missing":
        rows.pop()
    elif mutation == "expected":
        rows[0]["expected_route"] = "different"
    elif mutation == "passed":
        rows[0]["passed"] = 1
    else:
        rows[0]["raw_reply"] = "a" * (evaluator.MAX_REPLY_BYTES + 1)
    with pytest.raises(ValueError):
        gate.check_report(report, MODEL, ENDPOINT)


@pytest.mark.parametrize("body", [b'\xff', b'{', b'{"mode":"smoke","mode":"official"}',
                                    b'{"n":NaN}', b'{"n":Infinity}',
                                    b'{"x":' + b'[' * 12 + b'0' + b']' * 12 + b'}',
                                    b' ' * (evaluator.MAX_REPORT_BYTES + 1)],
                         ids=["nonutf8", "truncated", "duplicate", "nan", "inf", "depth", "oversize"])
def test_gate_cli_unsafe_json_rejected_safely(tmp_path, body):
    path = tmp_path / "bad.json"
    path.write_bytes(body)
    result = cli(path)
    assert result.returncode == 2
    assert "AGENT_GATE_ERROR:invalid_input_or_identity" in result.stdout.splitlines()
    assert "Traceback" not in result.stderr


def test_route_threshold_boundary_and_raw_recomputation(report):
    report["cases"][0]["raw_reply"] = "YES"
    probe.recompute(report)
    assert report["route_accuracy"] == 0.9
    assert gate.check_report(report, MODEL, ENDPOINT) == []
    report["cases"][1]["raw_reply"] = "YES"
    probe.recompute(report)
    assert gate.check_report(report, MODEL, ENDPOINT) == ["route_accuracy"]


def test_probe_real_subprocess_three_target_metrics(report, tmp_path):
    original = copy.deepcopy(report)
    manifest = probe.run_probes(report, MODEL, ENDPOINT, tmp_path / "synthetic-probes")
    assert report == original
    assert len(manifest["results"]) == 3
    assert all(row["exit_code"] == 1 for row in manifest["results"])
    assert "not-model-quality" in manifest["evidence"]
    assert (tmp_path / "synthetic-probes/manifest.json").exists()
    with pytest.raises(FileExistsError):
        probe.run_probes(report, MODEL, ENDPOINT, tmp_path / "synthetic-probes")


def test_probe_route_mutation_selects_correct_cases_at_threshold(report, tmp_path):
    report["cases"][0]["raw_reply"] = "YES"
    probe.recompute(report)
    assert report["route_accuracy"] == 0.9
    manifest = probe.run_probes(report, MODEL, ENDPOINT, tmp_path / "synthetic-probes")
    assert manifest["results"][0]["reason_codes"] == ["AGENT_GATE_METRIC:route_accuracy"]


@pytest.mark.parametrize("exit_code,stdout", [(0, "AGENT_GATE_PASS"), (2, "AGENT_GATE_ERROR:invalid"),
                                                (1, "AGENT_GATE_METRIC:followup_recall")])
def test_probe_rejects_nonmetric_or_wrongmetric_failure(report, tmp_path, monkeypatch, exit_code, stdout):
    monkeypatch.setattr(evaluator, "git_commit", lambda: report["git_commit"])
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], exit_code, stdout, ""))
    with pytest.raises(ValueError, match="target_metric"):
        probe.run_probes(report, MODEL, ENDPOINT, tmp_path / "synthetic-probes")
    assert not (tmp_path / "synthetic-probes/manifest.json").exists()


def test_probe_rejects_bad_base_without_creating_output(report, tmp_path):
    report["mode"] = "smoke"
    with pytest.raises(ValueError):
        probe.run_probes(report, MODEL, ENDPOINT, tmp_path / "synthetic-probes")
    assert not (tmp_path / "synthetic-probes").exists()


def test_gate_expectations_are_independent(report):
    with pytest.raises(ValueError, match="model_identity"):
        gate.check_report(report, "another-real-model", ENDPOINT)
    with pytest.raises(ValueError, match="endpoint_identity"):
        gate.check_report(report, MODEL, "a" * 64)


def test_gate_counts_independently_of_evaluator_scorer(report, monkeypatch):
    report["cases"][0]["raw_reply"] = "YES"
    probe.recompute(report)
    report["route_accuracy"] = 1.0
    monkeypatch.setattr(evaluator, "score", lambda *a: {"route_accuracy": 1.0, "followup_recall": 1.0})
    with pytest.raises(ValueError, match="metric_integrity"):
        gate.check_report(report, MODEL, ENDPOINT)


def test_gate_rejects_changed_frozen_dataset(report, monkeypatch):
    monkeypatch.setattr(evaluator, "dataset_hash", lambda: "a" * 64)
    with pytest.raises(ValueError, match="frozen_contract"):
        gate.check_report(report, MODEL, ENDPOINT)


def test_gate_rejects_wrong_git_identity(report):
    report["git_commit"] = "a" * 40
    with pytest.raises(ValueError, match="git_identity"):
        gate.check_report(report, MODEL, ENDPOINT)


def test_read_report_accepts_exact_size_limit(tmp_path):
    path = tmp_path / "boundary.json"
    body = b'{}' + b' ' * (evaluator.MAX_REPORT_BYTES - 2)
    path.write_bytes(body)
    assert gate.read_report(path) == {}


def test_gate_reply_limit_in_bytes(report):
    report["cases"][0]["raw_reply"] = "NO" + " " * (evaluator.MAX_REPLY_BYTES - 2)
    assert gate.check_report(report, MODEL, ENDPOINT) == []
    report["cases"][0]["raw_reply"] += "中"
    with pytest.raises(ValueError, match="reply_size"):
        gate.check_report(report, MODEL, ENDPOINT)


def test_cli_model_corruption_does_not_echo_sensitive_text(report, tmp_path):
    report["model"] = "sensitive-marker"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    result = cli(path)
    assert result.returncode == 2
    assert "sensitive-marker" not in result.stdout + result.stderr
