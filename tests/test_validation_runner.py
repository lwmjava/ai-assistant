"""Contracts for safe selection and failure-preserving check execution."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import validate_delivery as runner

CI_WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"


@pytest.mark.parametrize("selection", ["../outside.py", "--collect-only", "tests/missing.py", "/etc/passwd"])
def test_selection_rejects_missing_options_and_external_paths(tmp_path, selection):
    with pytest.raises(ValueError):
        runner.select_tests(tmp_path, [selection])


def test_selection_preserves_explicit_node_and_removes_duplicates(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_example.py").write_text("", encoding="utf-8")
    selection = "tests/test_example.py::test_boundary"
    assert runner.select_tests(tmp_path, [selection, selection]) == [selection]


def test_empty_selection_cannot_pass(tmp_path):
    with pytest.raises(ValueError):
        runner.select_tests(tmp_path, [])


def test_command_retains_failure_exit_and_output(tmp_path):
    log = tmp_path / "failed.log"
    result = runner.run_command(
        [sys.executable, "-c", "print('actual failure'); raise SystemExit(7)"],
        tmp_path, log, dict(os.environ),
    )
    assert result["exit_code"] == 7
    assert result["status"] == "failed"
    assert "actual failure" in log.read_text(encoding="utf-8")


def test_timed_out_command_never_passes(tmp_path):
    result = runner.run_command(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        tmp_path, tmp_path / "timeout.log", dict(os.environ), timeout=0.1,
    )
    assert result["status"] == "timeout"
    assert result["exit_code"] != 0


def test_cli_rejects_empty_target_without_launching_suite():
    result = subprocess.run(
        [sys.executable, str(runner.__file__), "targeted"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "--test" in result.stderr


def test_repeated_permission_errors_stop_before_the_timeout(tmp_path):
    script = (
        "import time\n"
        "for _ in range(5):\n"
        "    print('E   PermissionError: [WinError 5] Access is denied: data/pytest-tmp/run')\n"
        "time.sleep(30)\n"
    )
    result = runner.run_command(
        [sys.executable, "-u", "-c", script],
        tmp_path, tmp_path / "flood.log", dict(os.environ), timeout=10,
    )
    assert result["status"] == "infrastructure_error"
    assert result["elapsed_seconds"] < 8
    assert "PermissionError" in result["sample"]


def test_cli_summary_rejects_unknown_run_without_traceback(tmp_path):
    missing = tmp_path / "not-a-run"
    missing.mkdir()
    result = subprocess.run(
        [sys.executable, str(runner.__file__), "summary", "--root", str(tmp_path), "--run", str(missing)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "data/validation" in result.stderr


def test_cli_summary_never_promotes_invalidated_evidence(tmp_path):
    run = runner.create_run(tmp_path)
    document = {"status": "invalidated", "commands": [], "inputs": {}}
    (run / "result.json").write_text(json.dumps(document), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(runner.__file__), "summary", "--root", str(tmp_path), "--run", str(run)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "invalidated"


def _ci_workflow():
    return yaml.load(CI_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _named_step(job, name):
    return next(step for step in job["steps"] if step.get("name") == name)


def test_ci_keeps_backend_rag_and_does_not_enable_duplicate_job_by_default():
    workflow = _ci_workflow()
    assert set(workflow["on"]) == {"push", "pull_request"}
    assert workflow["permissions"] == {"contents": "read"}
    backend = workflow["jobs"]["backend"]
    rag = workflow["jobs"]["rag-baseline"]
    assert backend["name"] == "Backend (ruff / mypy / pytest)"
    assert rag["name"] == "RAG official baseline"
    assert "needs" not in rag
    assert rag["if"] == "vars.ENABLE_RAG_OFFICIAL_JOB == 'true'"
    backend_rag = _named_step(backend, "RAG baseline gate")
    independent_rag = _named_step(rag, "RAG baseline gate")
    assert "if" not in backend
    assert "if" not in backend_rag
    assert "continue-on-error" not in backend_rag
    assert "continue-on-error" not in independent_rag
    for step in (backend_rag, independent_rag):
        assert "run_rag_baseline.py --mode official" in step["run"]
        assert "rag_gate_check.py" in step["run"]
        assert "rag-v0.1-baseline-20260919.json" in step["run"]
        assert step["env"]["EMBEDDING_API_KEY"] == "${{ secrets.EMBEDDING_API_KEY }}"
    pytest_step = _named_step(backend, "Pytest")
    assert "EMBEDDING_API_KEY" not in str(pytest_step)
    assert "--junitxml=data/validation/ci-backend/pytest.xml" in pytest_step["run"]


def test_ci_python_jobs_use_pip_cache_and_upload_non_secret_evidence():
    workflow = _ci_workflow()
    for job_name in ("backend", "rag-baseline", "agent-baseline"):
        setup = next(
            step for step in workflow["jobs"][job_name]["steps"]
            if str(step.get("uses", "")).startswith("actions/setup-python")
        )
        assert setup["with"]["cache"] == "pip"
        cache_path = setup["with"]["cache-dependency-path"]
        assert "requirements.txt" in cache_path
        assert "pyproject.toml" in cache_path
    backend_artifact = _named_step(workflow["jobs"]["backend"], "Upload Backend evidence")
    rag_artifact = _named_step(workflow["jobs"]["rag-baseline"], "Upload RAG evidence")
    assert backend_artifact["if"] == "always()"
    assert rag_artifact["if"] == "always()"
    assert backend_artifact["with"]["path"] == "data/validation/ci-backend/pytest.xml"
    assert rag_artifact["with"]["path"] == "evals/reports/rag-v0.1-baseline-ci.json"
    assert int(backend_artifact["with"]["retention-days"]) == 14
    assert int(rag_artifact["with"]["retention-days"]) == 14
    assert "EMBEDDING_API_KEY" not in str(backend_artifact)
    assert "LLM_API_KEY" not in str(rag_artifact)
