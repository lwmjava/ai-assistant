"""Isolation, locking and input-identity regression checks."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import validate_delivery as runner
from tests._utf8_subprocess import run_utf8


def test_runs_are_unique_and_preflight_uses_only_its_own_database(tmp_path):
    first = runner.create_run(tmp_path)
    second = runner.create_run(tmp_path)
    assert first != second
    assert first.is_relative_to(tmp_path / "data/validation")
    result = runner.preflight(tmp_path, first)
    assert result["database_url"].startswith("sqlite:///")
    assert first.as_posix() in result["database_url"]
    assert not (tmp_path / "data/ai_assistant.db").exists()


def test_preflight_refuses_external_run_directory(tmp_path):
    with pytest.raises(ValueError):
        runner.preflight(tmp_path, tmp_path / "elsewhere")


def test_full_lock_excludes_another_runner_and_releases_on_error(tmp_path):
    first, second = runner.create_run(tmp_path), runner.create_run(tmp_path)
    with pytest.raises(RuntimeError, match="expected failure"):
        with runner.WorkspaceLock(tmp_path, first):
            with pytest.raises(runner.LockHeldError):
                with runner.WorkspaceLock(tmp_path, second):
                    pytest.fail("second full suite started")
            raise RuntimeError("expected failure")
    with runner.WorkspaceLock(tmp_path, second):
        lock = json.loads((tmp_path / "data/validation/full.lock").read_text(encoding="utf-8"))
        assert lock["pid"] == os.getpid()
        assert lock["run_id"] == second.name


def test_stale_lock_is_not_automatically_deleted(tmp_path):
    run = runner.create_run(tmp_path)
    lock = tmp_path / "data/validation/full.lock"
    lock.write_text('{"pid": 99999999, "run_id": "old"}', encoding="utf-8")
    with pytest.raises(runner.LockHeldError) as caught:
        with runner.WorkspaceLock(tmp_path, run):
            pytest.fail("stale lock was silently bypassed")
    assert caught.value.occupant["run_id"] == "old"
    assert "已有全量进行中" in str(caught.value)
    assert json.loads(lock.read_text(encoding="utf-8"))["run_id"] == "old"


def test_cli_reports_existing_full_run_without_starting_pytest(tmp_path):
    lock = tmp_path / "data/validation/full.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps({"pid": 4242, "run_id": "occupied", "started_at": "2026-10-22T00:00:00+00:00"}),
        encoding="utf-8",
    )
    completed = run_utf8(
        [sys.executable, str(runner.__file__), "full", "--root", str(tmp_path)],
        timeout=30,
        check=False,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert completed.returncode != 0
    assert "Traceback" not in (completed.stderr or "")
    published = json.loads(completed.stdout or "")
    assert published["status"] == "lock_held"
    assert published["occupant"]["run_id"] == "occupied"
    assert published["occupant"]["pid"] == 4242
    recorded = json.loads((Path(published["run"]) / "result.json").read_text(encoding="utf-8"))
    assert recorded["commands"] == []
    assert not (Path(published["run"]) / "pytest.log").exists()
    assert json.loads(lock.read_text(encoding="utf-8"))["run_id"] == "occupied"


def test_snapshot_changes_for_added_modified_and_deleted_inputs(tmp_path):
    source = tmp_path / "app/example.py"
    source.parent.mkdir()
    source.write_text("value = 1", encoding="utf-8")
    before = runner.input_snapshot(tmp_path, ["backend"])
    source.write_text("value = 2", encoding="utf-8")
    assert runner.input_snapshot(tmp_path, ["backend"]) != before
    source.unlink()
    assert runner.input_snapshot(tmp_path, ["backend"]) != before


def test_frontend_changes_do_not_invalidate_backend_evidence(tmp_path):
    source = tmp_path / "frontend/src/Page.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("first", encoding="utf-8")
    backend = runner.input_snapshot(tmp_path, ["backend"])
    frontend = runner.input_snapshot(tmp_path, ["frontend"])
    source.write_text("second", encoding="utf-8")
    assert runner.input_snapshot(tmp_path, ["backend"]) == backend
    assert runner.input_snapshot(tmp_path, ["frontend"]) != frontend


def test_api_contract_changes_invalidate_frontend_evidence(tmp_path):
    source = tmp_path / "app/schemas/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("old", encoding="utf-8")
    before = runner.input_snapshot(tmp_path, ["frontend"])
    source.write_text("new", encoding="utf-8")
    assert runner.input_snapshot(tmp_path, ["frontend"]) != before


def test_private_environment_is_not_read_as_a_check_input(tmp_path):
    private = tmp_path / ".env"
    private.write_text("PRIVATE=not-an-input", encoding="utf-8")
    snapshot = runner.input_snapshot(tmp_path, ["backend", "frontend"])
    assert ".env" not in snapshot


def test_run_environment_cannot_inherit_business_database_or_model_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/ai_assistant.db")
    monkeypatch.setenv("EMBEDDING_API_KEY", "synthetic-secret")
    run = runner.create_run(tmp_path)
    environment = runner.check_environment(run)
    assert run.as_posix() in environment["DATABASE_URL"]
    assert environment["EMBEDDING_API_KEY"] == ""
    assert environment["PYTHONUTF8"] == "1"


def test_executable_documents_belong_to_backend_inputs(tmp_path):
    script = tmp_path / "docs/reviews/probe.py"
    script.parent.mkdir(parents=True)
    script.write_text("value = 1\n", encoding="utf-8")
    before = runner.input_snapshot(tmp_path, ["backend"])
    assert "docs/reviews/probe.py" in before
    script.write_text("value = 2\n", encoding="utf-8")
    assert runner.input_snapshot(tmp_path, ["backend"]) != before
    assert "docs/reviews/probe.py" not in runner.input_snapshot(tmp_path, ["frontend"])


def test_frontend_icon_does_not_invalidate_backend_evidence(tmp_path):
    icon = tmp_path / "frontend/src/mark.svg"
    icon.parent.mkdir(parents=True)
    icon.write_text("<svg/>", encoding="utf-8")
    backend = runner.input_snapshot(tmp_path, ["backend"])
    frontend = runner.input_snapshot(tmp_path, ["frontend"])
    assert "frontend/src/mark.svg" in frontend
    icon.write_text("<svg id='x'/>", encoding="utf-8")
    assert runner.input_snapshot(tmp_path, ["backend"]) == backend
    assert runner.input_snapshot(tmp_path, ["frontend"]) != frontend


def test_input_change_invalidates_even_when_command_passes(tmp_path):
    source = tmp_path / "app/sample.py"
    source.parent.mkdir()
    source.write_text("one", encoding="utf-8")
    scopes = ["backend"]
    result = {"status": "running", "inputs": runner.input_snapshot(tmp_path, scopes), "commands": []}
    source.write_text("two", encoding="utf-8")
    runner.record_outcome(result, {"status": "passed", "exit_code": 0}, runner.input_snapshot(tmp_path, scopes))
    assert result["status"] == "invalidated"


def test_summary_persists_invalidated_status(tmp_path):
    source = tmp_path / "app/sample.py"
    source.parent.mkdir()
    source.write_text("one", encoding="utf-8")
    run = runner.create_run(tmp_path)
    document = {
        "status": "passed",
        "inputs": runner.input_snapshot(tmp_path, ["backend"]),
        "scopes": ["backend"],
        "commands": [],
    }
    (run / "result.json").write_text(json.dumps(document), encoding="utf-8")
    source.write_text("two", encoding="utf-8")
    result = runner.summarize(tmp_path, run)
    assert result["status"] == "invalidated"
    assert json.loads((run / "result.json").read_text(encoding="utf-8"))["status"] == "invalidated"


def test_finished_process_is_interrupted_without_clearing_its_lock(tmp_path):
    completed = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])
    completed.wait(timeout=10)
    assert runner.process_exists(completed.pid) is False
    run = runner.create_run(tmp_path)
    document = {"status": "running", "pid": completed.pid, "inputs": {}, "scopes": [], "commands": []}
    (run / "result.json").write_text(json.dumps(document), encoding="utf-8")
    lock = tmp_path / "data/validation/full.lock"
    lock.write_text(json.dumps({"pid": completed.pid, "run_id": "stale", "token": "keep"}), encoding="utf-8")
    result = runner.summarize(tmp_path, run)
    assert result["status"] == "interrupted"
    assert "不要结束无关进程" in result["recovery"]
    assert json.loads(lock.read_text(encoding="utf-8"))["token"] == "keep"
    assert json.loads((run / "result.json").read_text(encoding="utf-8"))["status"] == "interrupted"


def test_live_process_is_not_marked_interrupted(tmp_path):
    run = runner.create_run(tmp_path)
    document = {"status": "running", "pid": os.getpid(), "inputs": {}, "scopes": [], "commands": []}
    (run / "result.json").write_text(json.dumps(document), encoding="utf-8")
    result = runner.summarize(tmp_path, run)
    assert result["status"] == "running"
    assert "full.lock" in result["recovery"]
    assert json.loads((run / "result.json").read_text(encoding="utf-8"))["status"] == "running"


def test_preflight_records_checker_versions_and_revision(tmp_path):
    run = runner.create_run(tmp_path)
    observed = runner.preflight(tmp_path, run)
    assert all(observed["packages"][name] for name in ("pytest", "ruff", "mypy", "sqlmodel"))
    assert observed["declared_extras"] == []
    revision = runner.source_revision(runner.ROOT)
    assert observed["revision"]["head"] == revision["head"]
    assert isinstance(revision["head"], str) and len(revision["head"]) == 40
    assert "dev" in runner.declared_extras(runner.ROOT)


def test_default_pytest_basetemp_is_not_the_shared_run_folder():
    from tests import conftest

    assert conftest._PYTEST_RUN.name != "run"
    assert conftest._PYTEST_RUN.is_relative_to(Path(conftest._PYTEST_TMP))
