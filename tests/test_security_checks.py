"""Real staged commits are rejected for new synthetic credentials."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import security_checks as checks

ROOT = Path(__file__).resolve().parents[1]


def command(repo, *args, env=None):
    return subprocess.run(list(args), cwd=repo, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)


@pytest.fixture
def protected_repo(tmp_path, request):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ, PRE_COMMIT_HOME=str(tmp_path / "hook-cache"),
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent),
                                   str(Path(sys.executable).parent / "Scripts"), env["PATH"]])
    for args in [("init", "-q"), ("config", "user.name", "Synthetic Test"),
                 ("config", "user.email", "synthetic@example.invalid")]:
        assert command(repo, "git", *args, env=env).returncode == 0
    (repo / "safe.py").write_text("print(1)\n", encoding="utf-8")
    scenario = getattr(request, "param", False)
    if scenario is True:
        old_secret = "".join(("AKIA", "A9S8D7F6G5H4J3K2"))
        (repo / "legacy.py").write_text(f"credential = '{old_secret}'\n", encoding="utf-8")
    config = ROOT / ".pre-commit-config.yaml"
    if config.exists():
        shutil.copy2(config, repo / config.name)
        (repo / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/security_checks.py", repo / "scripts/security_checks.py")
    assert command(repo, "git", "add", ".", env=env).returncode == 0
    assert command(repo, "git", "commit", "-qm", "Initial synthetic fixture", env=env).returncode == 0
    if config.exists() and scenario != "ci-only":
        assert command(repo, sys.executable, "-m", "pre_commit", "install", env=env).returncode == 0
    return repo, env


def test_commit_with_new_synthetic_secret_is_rejected(protected_repo):
    repo, env = protected_repo
    secret = "".join(("AKIA", "Q7W9E2R4T6Y8U3I5"))
    (repo / "leak.py").write_text(f"credential = '{secret}'\n", encoding="utf-8")
    assert command(repo, "git", "add", "leak.py", env=env).returncode == 0
    result = command(repo, "git", "commit", "-qm", "Synthetic credential", env=env)
    assert result.returncode != 0, "A real commit containing a new synthetic secret must be blocked"
    assert secret not in result.stdout + result.stderr


def test_clean_commit_succeeds(protected_repo):
    repo, env = protected_repo
    (repo / "safe.py").write_text("print(2)\n", encoding="utf-8")
    assert command(repo, "git", "add", "safe.py", env=env).returncode == 0
    result = command(repo, "git", "commit", "-qm", "Clean synthetic change", env=env)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("protected_repo", [True], indirect=True)
@pytest.mark.parametrize("rename", [False, True])
def test_existing_value_is_not_retroactively_blocked(protected_repo, rename):
    repo, env = protected_repo
    if rename:
        assert command(repo, "git", "mv", "legacy.py", "renamed.py", env=env).returncode == 0
    else:
        with (repo / "legacy.py").open("a", encoding="utf-8") as handle:
            handle.write("print(2)\n")
        assert command(repo, "git", "add", "legacy.py", env=env).returncode == 0
    result = command(repo, "git", "commit", "-qm", "Existing fixture change", env=env)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("staged_secret", [False, True])
def test_scan_uses_index_instead_of_unstaged_contents(protected_repo, staged_secret):
    repo, env = protected_repo
    secret = "".join(("AKIA", "Z9X8C7V6B5N4M3L2"))
    payload = f"credential = '{secret}'\n"
    (repo / "safe.py").write_text(payload if staged_secret else "print(2)\n", encoding="utf-8")
    assert command(repo, "git", "add", "safe.py", env=env).returncode == 0
    (repo / "safe.py").write_text("print(3)\n" if staged_secret else payload, encoding="utf-8")
    result = command(repo, sys.executable, "scripts/security_checks.py", "secrets", "--staged", env=env)
    assert result.returncode == int(staged_secret), result.stdout + result.stderr
    assert secret not in result.stdout + result.stderr


def test_ci_range_checks_new_committed_blobs(protected_repo):
    repo, env = protected_repo
    base = command(repo, "git", "rev-parse", "HEAD", env=env).stdout.strip()
    (repo / "safe.py").write_text("print(2)\n", encoding="utf-8")
    assert command(repo, "git", "add", "safe.py", env=env).returncode == 0
    assert command(repo, "git", "commit", "-qm", "Clean range", env=env).returncode == 0
    result = command(repo, sys.executable, "scripts/security_checks.py", "secrets", "--base-ref", base, env=env)
    assert result.returncode == 0
    assert json.loads(result.stdout)["files_checked"] == 1


@pytest.mark.parametrize("protected_repo", ["ci-only"], indirect=True)
def test_secret_in_new_commit_is_blocked_even_if_removed_later(protected_repo):
    repo, env = protected_repo
    base = command(repo, "git", "rev-parse", "HEAD", env=env).stdout.strip()
    secret = "".join(("AKIA", "P2O3I4U5Y6T7R8E9"))
    (repo / "leak.py").write_text(f"credential = '{secret}'\n", encoding="utf-8")
    assert command(repo, "git", "add", "leak.py", env=env).returncode == 0
    assert command(repo, "git", "commit", "-qm", "Incoming synthetic credential", env=env).returncode == 0
    assert command(repo, "git", "rm", "leak.py", env=env).returncode == 0
    assert command(repo, "git", "commit", "-qm", "Remove from incoming tip", env=env).returncode == 0
    result = command(repo, sys.executable, "scripts/security_checks.py", "secrets", "--base-ref", base, env=env)
    assert result.returncode == 1
    assert "leak.py" in result.stdout and secret not in result.stdout + result.stderr


def test_environment_file_is_rejected_without_reading_it(protected_repo, monkeypatch):
    repo, env = protected_repo
    (repo / ".env").write_text("synthetic only", encoding="utf-8")
    assert command(repo, "git", "add", ".env", env=env).returncode == 0
    actual = checks.git

    def guarded(root, *args, **kwargs):
        assert not (args[0] == "show" and args[-1].endswith(":.env"))
        return actual(root, *args, **kwargs)

    monkeypatch.setattr(checks, "git", guarded)
    report = checks.scan_secrets(repo, staged=True, base_ref=None)
    assert report["status"] == "failed"
    assert report["findings"][0]["type"] == "forbidden_environment_file"


def test_unexpected_scanner_failure_is_safe_and_nonzero(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("private scanner input")

    monkeypatch.setattr(checks, "scan_secrets", fail)
    assert checks.main(["secrets", "--staged"]) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "error", "code": "security_check_incomplete"}


def test_secret_scanning_does_not_verify_credentials_online(monkeypatch):
    import requests

    def forbidden(*args, **kwargs):
        pytest.fail("Credential verification must not make network requests")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    secret = "".join(("AKIA", "L2K3J4H5G6F7D8S9"))
    found = checks.scan_blob(f"credential = '{secret}'\n".encode(), "probe.py")
    assert any(kind == "AWS Access Key" for kind, _ in found)


def test_unavailable_base_cannot_be_reported_as_clean(protected_repo):
    repo, env = protected_repo
    result = command(repo, sys.executable, "scripts/security_checks.py", "secrets", "--base-ref", "a" * 40, env=env)
    assert result.returncode == 2


def test_audit_output_cannot_overwrite_source(tmp_path):
    protected = tmp_path / "keep.py"
    protected.write_text("preserve", encoding="utf-8")
    with pytest.raises(checks.CheckError):
        checks.audit_output(tmp_path, str(protected), "audit")
    assert protected.read_text(encoding="utf-8") == "preserve"


def test_python_manifest_covers_both_inputs_and_ci_extras():
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    text = checks.python_requirements(ROOT)
    assert "requirements.txt" in text
    for requirement in project["dependencies"] + project["optional-dependencies"]["dev"]:
        assert requirement in text
    assert project["optional-dependencies"]["langgraph"][0] in text


@pytest.mark.parametrize("returncode,vulnerabilities,expected", [(0, 0, "passed"), (1, 1, "failed")])
def test_python_audit_retains_vulnerability_failure(tmp_path, monkeypatch, returncode, vulnerabilities, expected):
    output = checks.audit_output(tmp_path, None, "python-audit")
    monkeypatch.setattr(checks, "python_requirements", lambda root: "example==1.0\n")

    def run(argv, **kwargs):
        assert "--strict" in argv and not any(arg in argv for arg in ("--fix", "--ignore-vuln"))
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        output.write_text(json.dumps({"dependencies": [{"name": "example", "version": "1.0",
                                                       "vulns": [{"id": "synthetic"}] * vulnerabilities}]}))
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(checks.subprocess, "run", run)
    assert checks.audit_python(tmp_path, output)["status"] == expected


@pytest.mark.parametrize("report", [{}, {"dependencies": []}, {"dependencies": [{"skip_reason": "unavailable"}]}])
def test_incomplete_python_audit_cannot_pass(tmp_path, monkeypatch, report):
    output = checks.audit_output(tmp_path, None, "python-audit")
    monkeypatch.setattr(checks, "python_requirements", lambda root: "example==1.0\n")

    def run(*args, **kwargs):
        output.write_text(json.dumps(report))
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(checks.subprocess, "run", run)
    with pytest.raises(checks.CheckError):
        checks.audit_python(tmp_path, output)


@pytest.mark.parametrize("returncode,count,expected", [(0, 0, "passed"), (1, 2, "failed")])
def test_npm_audit_retains_failure(tmp_path, monkeypatch, returncode, count, expected):
    output = checks.audit_output(tmp_path, None, "npm-audit")
    monkeypatch.setattr(checks.shutil, "which", lambda name: "npm")
    monkeypatch.setattr(checks, "os", SimpleNamespace(name="posix"))

    def run(argv, **kwargs):
        assert "--ignore-scripts" in argv and "--package-lock-only" in argv and "fix" not in argv
        return SimpleNamespace(returncode=returncode,
                               stdout=json.dumps({"metadata": {"vulnerabilities": {"total": count}}}).encode())

    monkeypatch.setattr(checks.subprocess, "run", run)
    assert checks.audit_npm(tmp_path, output)["status"] == expected


def test_windows_npm_launcher_uses_node_without_a_shell(tmp_path, monkeypatch):
    npm = tmp_path / "Node Program Files/npm.cmd"
    cli = npm.parent / "node_modules/npm/bin/npm-cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("synthetic fixture", encoding="utf-8")
    monkeypatch.setattr(checks.shutil, "which", lambda name: str(npm) if name == "npm" else "node.exe")

    def run(argv, **kwargs):
        assert argv[:2] == ["node.exe", str(cli)] and not kwargs.get("shell")
        return SimpleNamespace(returncode=0, stdout=b'{"metadata":{"vulnerabilities":{"total":0}}}')

    monkeypatch.setattr(checks.subprocess, "run", run)
    output = checks.audit_output(tmp_path, None, "npm-audit")
    assert checks.audit_npm(tmp_path, output)["status"] == "passed"


def test_security_ci_is_independent_and_does_not_mask_failure():
    workflow = yaml.safe_load((ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8"))
    assert set(workflow.get("on", workflow.get(True))) == {"push", "pull_request"}
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert "continue-on-error" not in job and "if" not in job
        for step in job["steps"]:
            assert "continue-on-error" not in step
            assert "--fix" not in step.get("run", "") and "|| true" not in step.get("run", "")
    secret_steps = workflow["jobs"]["secrets"]["steps"]
    assert secret_steps[0]["with"]["fetch-depth"] == 0
    assert any("--base-ref" in step.get("run", "") for step in secret_steps)
    steps = workflow["jobs"]["dependencies"]["steps"]
    assert next(step for step in steps if step.get("name") == "npm audit")["if"] == "${{ !cancelled() }}"


def test_hook_scopes_include_manifests_and_gate_configuration():
    import re

    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {hook["id"]: hook for hook in config["repos"][0]["hooks"]}
    assert hooks["staged-secrets"]["always_run"] and not hooks["staged-secrets"]["pass_filenames"]
    for path in ["requirements.txt", "pyproject.toml", "scripts/security_checks.py", ".pre-commit-config.yaml"]:
        assert re.search(hooks["python-dependency-audit"]["files"], path)
    for path in ["frontend/package-lock.json", "frontend/package.json", "scripts/security_checks.py"]:
        assert re.search(hooks["npm-dependency-audit"]["files"], path)
    assert not re.search(hooks["python-dependency-audit"]["files"], "app/main.py")


def test_security_tool_versions_match_ci_installation():
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    workflow = (ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8")
    for name in ["detect-secrets", "pip-audit"]:
        pin = next(item for item in project["optional-dependencies"]["dev"] if item.startswith(name + "=="))
        assert pin in workflow
