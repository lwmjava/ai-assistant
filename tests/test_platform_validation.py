"""Platform evidence must include successful real executions, never missing/skipped cases."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from scripts.check_platform_validation import verify


@pytest.fixture()
def evidence(tmp_path):
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps({"version": 1, "expected_count": 2, "cases": [
        {"classname": "tests.platform", "name": "first"},
        {"classname": "tests.platform", "name": "second"},
    ]}), encoding="utf-8")
    report = tmp_path / "junit.xml"
    suite = ET.Element("testsuite")
    for name in ("first", "second"):
        ET.SubElement(suite, "testcase", classname="tests.platform", name=name)
    return manifest, report, suite


def save(report, suite):
    ET.ElementTree(suite).write(report, encoding="utf-8")


def test_all_obligations_must_execute_successfully(evidence):
    manifest, report, suite = evidence
    save(report, suite)
    assert verify(report, manifest) == []


@pytest.mark.parametrize("tag", ["skipped", "failure", "error"])
def test_unsuccessful_case_cannot_pass_platform_gate(evidence, tag):
    manifest, report, suite = evidence
    ET.SubElement(suite[0], tag)
    save(report, suite)
    assert verify(report, manifest) == ["tests.platform::first"]


def test_missing_case_is_not_success(evidence):
    manifest, report, suite = evidence
    suite.remove(suite[0])
    save(report, suite)
    assert verify(report, manifest) == ["tests.platform::first"]


def test_duplicate_result_cannot_hide_failure(evidence):
    manifest, report, suite = evidence
    duplicate = ET.SubElement(suite, "testcase", classname="tests.platform", name="first")
    ET.SubElement(duplicate, "failure")
    save(report, suite)
    assert verify(report, manifest) == ["tests.platform::first"]


@pytest.mark.parametrize("cases,count", [([], 0), ([{"classname": "x", "name": "y"}] * 2, 2)])
def test_empty_or_duplicated_manifest_is_rejected(evidence, cases, count):
    manifest, report, suite = evidence
    manifest.write_text(json.dumps({"version": 1, "cases": cases, "expected_count": count}), encoding="utf-8")
    save(report, suite)
    with pytest.raises(ValueError):
        verify(report, manifest)


def test_original_obligations_cover_database_kernel_and_filesystem():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "tests/platform_validation_cases.json").read_text(encoding="utf-8"))
    assert data["expected_count"] == len(data["cases"]) == 31
    assert sum(case["classname"] == "tests.test_postgresql_integration" for case in data["cases"]) == 16
    assert sum(case["classname"] == "tests.test_sandbox_linux_limits" for case in data["cases"]) == 11


def test_backend_requires_real_linux_and_postgresql_before_accepting_evidence():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    backend = workflow["jobs"]["backend"]
    assert backend["runs-on"] == "ubuntu-latest"
    steps = backend["steps"]
    start = next(step for step in steps if step.get("name") == "Start disposable PostgreSQL validation")
    assert 'os.geteuid() != 0' in start["run"]
    assert '127.0.0.1::5432' in start["run"]
    assert 'POSTGRES_VALIDATION_REQUIRED=1' in start["run"]
    assert '::add-mask::' in start["run"]
    gate = next(step for step in steps if step.get("name") == "Require real platform coverage")
    assert 'check_platform_validation.py' in gate["run"]
    assert 'if' not in gate and not gate.get("continue-on-error", False)
    assert steps.index(gate) > next(i for i, step in enumerate(steps) if step.get("name") == "Pytest")
    assert steps.index(gate) < next(i for i, step in enumerate(steps) if step.get("name") == "RAG baseline gate")
    cleanup = next(step for step in steps if step.get("name") == "Stop disposable PostgreSQL")
    assert cleanup["if"] == "always()"
    assert cleanup["run"].index('test "$owner" = "$GITHUB_RUN_ID"') < cleanup["run"].index('docker rm --force')


@pytest.mark.parametrize("tag,code", [(None, 0), ("skipped", 1)])
def test_cli_propagates_platform_gate_result(evidence, monkeypatch, capsys, tag, code):
    import sys

    from scripts.check_platform_validation import main

    manifest, report, suite = evidence
    if tag is not None:
        ET.SubElement(suite[0], tag)
    save(report, suite)
    monkeypatch.setattr(sys, "argv", ["check", "--report", str(report), "--manifest", str(manifest)])
    assert main() == code
    output = capsys.readouterr().out
    assert ("PLATFORM_VALIDATION_PASS" in output) is (code == 0)


def test_malformed_report_fails_without_echoing_contents(evidence, monkeypatch, capsys):
    import sys

    from scripts.check_platform_validation import main

    manifest, report, suite = evidence
    report.write_text("private-marker-not-xml", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["check", "--report", str(report), "--manifest", str(manifest)])
    assert main() == 1
    assert "private-marker" not in capsys.readouterr().err


def test_ci_environment_file_contains_real_line_separators():
    import ast

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    step = next(x for x in workflow["jobs"]["backend"]["steps"]
                if x.get("name") == "Start disposable PostgreSQL validation")
    code = step["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    constants = [node.value for node in ast.walk(ast.parse(code)) if isinstance(node, ast.Constant)]
    assert "\nPOSTGRES_VALIDATION_REQUIRED=1\n" in constants



def test_postgresql_readiness_waits_for_tcp_not_initialization_socket():
    import ast

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    step = next(x for x in workflow["jobs"]["backend"]["steps"]
                if x.get("name") == "Start disposable PostgreSQL validation")
    code = step["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    commands = [[item.value for item in node.elts if isinstance(item, ast.Constant)]
                for node in ast.walk(ast.parse(code)) if isinstance(node, ast.List)]
    ready = next(command for command in commands if "pg_isready" in command)
    assert ready[ready.index("-h") + 1] == "127.0.0.1"
    assert ready[ready.index("-p") + 1] == "5432"


@pytest.mark.parametrize("job", ["backend", "rag-baseline", "agent-baseline"])
def test_every_billable_gate_has_a_failing_authorization_step(job):
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"][job]["steps"]
    paid = next(i for i, step in enumerate(steps)
                if step.get("name") in {"RAG baseline gate", "Agent official evaluation"})
    guard = steps[paid - 1]
    assert guard["name"] == "Require official evaluation authorization"
    assert "if" not in guard and not guard.get("continue-on-error", False)
    assert 'OFFICIAL_EVALUATION_NOT_AUTHORIZED' in guard["run"]
    assert 'OFFICIAL_EVALUATION_AUTHORIZED_SHA' in guard["env"]
    assert not workflow["jobs"][job].get("continue-on-error", False)


@pytest.mark.parametrize("fault", ["missing", "false", "blank_budget", "wrong_sha", None])
def test_official_guard_requires_explicit_budget_and_exact_commit(monkeypatch, fault):
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    guard = next(step for step in workflow["jobs"]["backend"]["steps"]
                 if step.get("name") == "Require official evaluation authorization")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("OFFICIAL_EVALUATION_AUTHORIZED", "true")
    monkeypatch.setenv("OFFICIAL_EVALUATION_BUDGET_REF", "synthetic-approved-record")
    monkeypatch.setenv("OFFICIAL_EVALUATION_AUTHORIZED_SHA", "a" * 40)
    if fault == "missing":
        monkeypatch.delenv("OFFICIAL_EVALUATION_AUTHORIZED")
    elif fault == "false":
        monkeypatch.setenv("OFFICIAL_EVALUATION_AUTHORIZED", "false")
    elif fault == "blank_budget":
        monkeypatch.setenv("OFFICIAL_EVALUATION_BUDGET_REF", "  ")
    elif fault == "wrong_sha":
        monkeypatch.setenv("OFFICIAL_EVALUATION_AUTHORIZED_SHA", "b" * 40)
    code = guard["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    if fault is not None:
        with pytest.raises(SystemExit, match="OFFICIAL_EVALUATION_NOT_AUTHORIZED"):
            exec(compile(code, "authorization_guard", "exec"), {})
    else:
        exec(compile(code, "authorization_guard", "exec"), {})
