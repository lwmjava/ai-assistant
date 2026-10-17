"""COV-001 isolated CLI counterexamples, not a production coverage-report service.

Run with the project conda interpreter. Writes only a fresh data/cov001-independent-*.
Synthetic fixtures prove measurement boundaries, never application coverage quality.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_number(report: dict) -> float:
    """Independent acceptance assertion for synthetic evidence rejection cases."""
    totals = report["totals"]
    total = totals["num_statements"]
    covered = totals["covered_lines"]
    percent = totals["percent_covered"]
    if type(total) is not int or total <= 0:
        raise ValueError("no_statements")
    if type(covered) is not int or not 0 <= covered <= total:
        raise ValueError("invalid_count")
    if type(percent) not in (int, float) or not math.isfinite(percent):
        raise ValueError("nonfinite_percent")
    exact = 100 * covered / total
    if not math.isclose(percent, exact, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError("inconsistent_percent")
    return exact


def verify_files(report: dict, expected: set[str]) -> None:
    if {p.replace("\\", "/") for p in report["files"]} != expected:
        raise ValueError("source_inventory_mismatch")


def rejected(action) -> None:
    try:
        action()
    except ValueError:
        return
    raise AssertionError("malformed evidence was accepted")


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix="cov001-independent-", dir=repo / "data"))
    app = root / "app"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "measured.py").write_text("def result():\n    return 1\n", encoding="utf-8")
    (app / "unimported.py").write_text("def untouched():\n    return 2\n", encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    safe_keys = ["LLM_API_KEY", "LLM_INTENT_API_KEY", "LLM_FALLBACK_API_KEY", "OPENAI_API_KEY", "EMBEDDING_API_KEY"]
    env = os.environ.copy()
    env.update(dict.fromkeys(safe_keys, ""))
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    for name in ("PYTEST_ADDOPTS", "COVERAGE_PROCESS_START", "COVERAGE_RCFILE"):
        env.pop(name, None)
    env["DATABASE_URL"] = "sqlite:///" + (root / "test_ai_assistant.db").as_posix()
    (root / "test_synthetic.py").write_text(
        "import os\nfrom app.measured import result\n"
        "def test_isolation():\n"
        f"    assert all(os.environ.get(k) == '' for k in {safe_keys!r})\n"
        "    assert os.environ['DATABASE_URL'].endswith('/test_ai_assistant.db')\n"
        "    assert result() == 1\n", encoding="utf-8",
    )
    commands = []

    def run(name: str, *, plugin: bool = True, expected_code: int = 0) -> dict | None:
        folder = root / name
        folder.mkdir()
        isolated = env.copy()
        isolated["COVERAGE_FILE"] = str(folder / ".coverage")
        args = [sys.executable, "-m", "pytest", "-c", "pytest.ini", "--rootdir=.", "--confcutdir=."]
        if plugin:
            args += ["-p", "pytest_cov"]
        args += ["test_synthetic.py", "--cov=app", "--cov-report=term-missing",
                 f"--cov-report=json:{folder / 'coverage.json'}", "--cov-fail-under=0", "-q"]
        proc = subprocess.run(args, cwd=root, env=isolated, capture_output=True, text=True, timeout=60)
        (folder / "terminal.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
        commands.append({"name": name, "args": args, "cwd": str(root), "exit_code": proc.returncode,
                         "coverage_file": isolated["COVERAGE_FILE"], "database_url": isolated["DATABASE_URL"]})
        assert proc.returncode == expected_code, (name, proc.returncode, proc.stdout, proc.stderr)
        report_file = folder / "coverage.json"
        return json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else None

    report = run("r1")
    assert report is not None
    exact = verify_number(report)
    expected_files = {p.relative_to(root).as_posix() for p in app.rglob("*.py")}
    verify_files(report, expected_files)
    unused_key = next(p for p in report["files"] if p.replace("\\", "/") == "app/unimported.py")
    assert report["files"][unused_key]["summary"]["covered_lines"] == 0
    assert exact < 100
    evidence_hashes = {str(p.relative_to(root)): digest(p) for p in (root / "r1").iterdir() if p.is_file()}
    source_hash = digest(app / "measured.py")
    missing = copy.deepcopy(report)
    del missing["files"][unused_key]
    rejected(lambda: verify_files(missing, expected_files))
    for value in (float("nan"), float("inf"), -float("inf"), 1e308):
        bad = copy.deepcopy(report)
        bad["totals"]["percent_covered"] = value
        rejected(lambda: verify_number(bad))
    bad = copy.deepcopy(report)
    bad["totals"]["num_statements"] = 0
    rejected(lambda: verify_number(bad))

    # Second full synthetic run does not append, erase, or overwrite first evidence.
    again = run("r2")
    assert again is not None and again["totals"] == report["totals"]
    assert all(digest(root / path) == value for path, value in evidence_hashes.items())
    assert (root / "r1" / ".coverage") != (root / "r2" / ".coverage")
    (app / "measured.py").write_text("def result():\n    return 9\n", encoding="utf-8")
    assert digest(app / "measured.py") != source_hash  # Old binding is stale, even if report still exists.
    failed = run("assertion_failure", expected_code=1)
    assert failed is not None and verify_number(failed) >= 0  # A number cannot mask pytest exit 1.
    missing_plugin = run("plugin_missing", plugin=False, expected_code=4)
    assert missing_plugin is None
    (root / "test_synthetic.py").write_text("def test_invalid(:\n    pass\n", encoding="utf-8")
    interrupted = run("collection_failure", expected_code=2)
    assert interrupted is None  # Collection abort does not supply a complete passing measurement.
    (root / "test_synthetic.py").write_text("def test_synthetic():\n    assert True\n", encoding="utf-8")
    empty_args = [sys.executable, "-m", "coverage", "run", "--source=absent_module", "-m", "pytest",
                  "-c", "pytest.ini", "--rootdir=.", "--confcutdir=.", "test_synthetic.py", "-q"]
    empty_env = env.copy()
    empty_env["COVERAGE_FILE"] = str(root / "no_data.coverage")
    empty = subprocess.run(empty_args, cwd=root, env=empty_env, capture_output=True, text=True, timeout=60)
    assert empty.returncode == 0
    json_args = [sys.executable, "-m", "coverage", "json", "-o", "no_data.json"]
    no_data = subprocess.run(json_args, cwd=root, env=empty_env, capture_output=True, text=True, timeout=60)
    assert no_data.returncode == 1 and not (root / "no_data.json").exists()
    commands.extend([{"name": "no_data_run", "args": empty_args, "cwd": str(root), "exit_code": empty.returncode},
                     {"name": "no_data_json", "args": json_args, "cwd": str(root), "exit_code": no_data.returncode}])
    (root / "no_data.log").write_text(empty.stdout + empty.stderr + no_data.stdout + no_data.stderr, encoding="utf-8")
    result = {"agent": "/root/cov_verify_review", "evidence_level": "E2 synthetic CLI boundaries only",
              "script_sha256": digest(Path(__file__)), "python": sys.version,
              "versions": {p: importlib.metadata.version(p) for p in ("pytest", "pytest-cov", "coverage")},
              "synthetic_exact_percent": exact, "commands": commands,
              "cases": ["finite_numeric_rejection", "zero_denominator_rejection", "missing_file_rejection",
                        "unimported_file_counted", "keys_and_test_database_isolation", "two_run_no_append",
                        "old_evidence_preserved", "stale_source_binding", "pytest_exit_1_preserved",
                        "missing_plugin_exit_4", "collection_failure_exit_2", "no_data_report_exit_1"],
              "fixture_sha256": {str(p.relative_to(root)): digest(p) for p in app.rglob("*.py")}}
    (root / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8",
    )
    print(root / "result.json")


if __name__ == "__main__":
    main()
