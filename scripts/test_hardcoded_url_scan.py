"""Hardcoded URL scan rejects non-allowlisted intranet and production URLs."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts import security_checks as checks

ROOT = Path(__file__).resolve().parents[1]


def write_allowlist(directory: Path, hosts=None, prefixes=None) -> Path:
    path = directory / "scripts" / "hardcoded_url_allowlist.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"hosts": hosts or ["example.com"], "url_prefixes": prefixes or []}),
        encoding="utf-8",
    )
    return path


def test_planted_intranet_and_production_urls_are_reported(tmp_path):
    allowlist = write_allowlist(tmp_path)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "leak.py").write_text(
        'INTRANET = "http://192.168.10.25/admin?token=should-redact"\n'
        'PRODUCTION = "https://billing.corp.internal/v1"\n',
        encoding="utf-8",
    )
    report = checks.scan_urls(tmp_path, allowlist=allowlist)
    assert report["status"] == "failed"
    assert {item["host"] for item in report["findings"]} == {"192.168.10.25", "billing.corp.internal"}
    assert {item["file"] for item in report["findings"]} == {"app/leak.py"}
    assert "http://192.168.10.25/admin" in {item["url"] for item in report["findings"]}
    assert all("token=" not in item["url"] and "should-redact" not in item["url"] for item in report["findings"])


def test_allowlisted_and_example_urls_pass(tmp_path):
    allowlist = write_allowlist(tmp_path, hosts=["cdn.docs.example"], prefixes=["https://allowed.docs.example/"])
    (tmp_path / "app").mkdir()
    (tmp_path / "frontend" / "src").mkdir(parents=True)
    (tmp_path / "app" / "ok.py").write_text(
        'DOCS = "https://example.com/guide"\n'
        'LOCAL = "http://127.0.0.1:8080/health"\n'
        'PREFIX = "https://allowed.docs.example/reference"\n'
        'TEMPLATE = "https://{raw}/path"\n',
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "src" / "hook.ts").write_text(
        'export const hook = "https://cdn.docs.example/hook";\n',
        encoding="utf-8",
    )
    report = checks.scan_urls(tmp_path, allowlist=allowlist)
    assert report["status"] == "passed"
    assert report["findings"] == []
    assert report["files_checked"] == 2


def test_non_allowlisted_hit_fails_the_gate(tmp_path, capsys):
    write_allowlist(tmp_path, hosts=["example.com"])
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "prod.py").write_text('API = "https://api.prod.internal/v2"\n', encoding="utf-8")
    code = checks.main(["urls", "--root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["status"] == "failed"
    assert payload["findings"][0]["host"] == "api.prod.internal"


def test_missing_allowlist_is_an_error_not_a_pass(tmp_path, capsys):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "ok.py").write_text("print(1)\n", encoding="utf-8")
    code = checks.main(["urls", "--root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload == {"status": "error", "code": "url_allowlist_unavailable"}


def test_environment_files_are_not_read(tmp_path, monkeypatch):
    allowlist = write_allowlist(tmp_path)
    (tmp_path / "app").mkdir()
    env_file = tmp_path / "app" / ".env"
    env_file.write_text("INTERNAL_URL=http://10.0.0.8/secret\n", encoding="utf-8")
    (tmp_path / "app" / "ok.py").write_text('URL = "https://example.com/ok"\n', encoding="utf-8")
    original = Path.read_text

    def guarded(self, *args, **kwargs):
        assert self != env_file
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    report = checks.scan_urls(tmp_path, allowlist=allowlist)
    assert report["status"] == "passed"
    assert report["files_checked"] == 1


def test_selftest_command_passes(capsys):
    assert checks.main(["urls", "--selftest"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "passed", "check": "urls-selftest"}


def test_current_repository_is_clean_against_checked_in_allowlist():
    report = checks.scan_urls(ROOT)
    assert report["status"] == "passed"
    assert report["findings"] == []
    assert report["files_checked"] > 0


def test_pre_commit_and_security_workflow_include_url_gate():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {hook["id"]: hook for hook in config["repos"][0]["hooks"]}
    hook = hooks["hardcoded-urls"]
    assert hook["entry"] == "python scripts/security_checks.py urls"
    assert hook["language"] == "system"
    assert not hook["pass_filenames"]
    assert hook.get("always_run") in {None, False}
    workflow = yaml.safe_load((ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["hardcoded-urls"]
    assert "continue-on-error" not in job and "if" not in job
    commands = [step.get("run", "") for step in job["steps"]]
    assert any("urls --selftest" in command for command in commands)
    assert any("scripts/security_checks.py urls" in command and "--selftest" not in command for command in commands)
    assert all("|| true" not in command and "--fix" not in command for command in commands)
