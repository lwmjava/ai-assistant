"""Independent E2 evidence through fresh CLI processes and isolated source trees."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "app/agents/version_registry.py"


@pytest.fixture
def isolated(tmp_path):
    root = tmp_path / "checkout"
    (root / "app/agents").mkdir(parents=True)
    (root / "app/core").mkdir()
    shutil.copyfile(ROOT / "app/core/config.py", root / "app/core/config.py")
    (root / "app/agents/prompts.py").write_text('SYSTEM_PROMPT = "initial"\n', encoding="utf-8")
    (root / ".env").write_text("INVALID_SYNTHETIC_ENV_SENTINEL", encoding="utf-8")
    registry = root / "registry.json"
    outsider = tmp_path / "outside"
    outsider.mkdir()
    trap = tmp_path / "trap"
    trap.mkdir()
    marker = tmp_path / "env-read-marker"
    (trap / "sitecustomize.py").write_text(
        "import os, sys\n"
        "def audit(event, args):\n"
        "    if event == 'open' and isinstance(args[0], (str, bytes)):\n"
        "        if os.path.basename(os.fsdecode(args[0])) != '.env':\n"
        "            return\n"
        f"        fd = os.open({str(marker)!r}, os.O_WRONLY | os.O_CREAT, 0o600)\n"
        "        os.write(fd, b'forbidden read')\n"
        "        os.close(fd)\n"
        "        raise RuntimeError('forbidden env read')\n"
        "sys.addaudithook(audit)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(trap), "LLM_DEFAULT_MODEL": "ambient-must-not-be-used"}

    def run(command, *arguments):
        result = subprocess.run(
            [sys.executable, str(CLI), command, "--root", str(root), "--registry", str(registry), *map(str, arguments)],
            cwd=outsider,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        assert not marker.exists(), "Direct CLI attempted to read .env"
        return result

    snapshot = run(
        "snapshot",
        "--version",
        "independent-v1",
        "--source-commit",
        "1" * 40,
        "--effective-at",
        "2026-10-11T00:00:00Z",
        "--rollback",
        "restore isolated sources",
    )
    assert snapshot.returncode == 0, snapshot.stderr
    record = json.loads(snapshot.stdout)
    registry.write_text(
        json.dumps({"schema_version": 1, "current": record["version"], "versions": [record]}), encoding="utf-8"
    )
    return root, registry, record, run


def assert_safe_failure(result):
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_cli_other_cwd_environment_trap_and_idempotence(isolated):
    _, registry, record, run = isolated
    original = registry.read_bytes()
    first, second = run("check"), run("check")
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["version"] == record["version"]
    assert record["model"]["fields"]["LLM_DEFAULT_MODEL"] != "ambient-must-not-be-used"
    assert registry.read_bytes() == original


@pytest.mark.parametrize("change", ["modify", "new-memory", "new-evolution", "new-rag", "delete"])
def test_source_drift_restore_actual_new_process(isolated, change):
    root, registry, _, run = isolated
    source = root / "app/agents/prompts.py"
    original, registration = source.read_bytes(), registry.read_bytes()
    extra = None
    if change == "modify":
        source.write_text('SYSTEM_PROMPT = "changed"\n', encoding="utf-8")
    elif change == "delete":
        source.unlink()
    else:
        extra = root / "app" / change.removeprefix("new-") / "new_prompt.py"
        extra.parent.mkdir()
        extra.write_text('PROMPT = "new entry"\n', encoding="utf-8")
    assert_safe_failure(run("check"))
    assert registry.read_bytes() == registration
    if extra:
        extra.unlink()
    source.write_bytes(original)
    assert run("check").returncode == 0


def test_lf_and_crlf_equivalent_source(isolated):
    root, _, _, run = isolated
    source = root / "app/agents/prompts.py"
    source.write_bytes(source.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert run("check").returncode == 0


@pytest.mark.parametrize(
    "key,value",
    [
        ("LLM_DEFAULT_MODEL", "distinct-model"),
        ("LLM_PROVIDER", "mock"),
        ("LLM_INTENT_FALLBACK_CHAIN", "fallback,chat"),
        ("LLM_TEMPERATURE", 0.125),
        ("LLM_BASE_URL", "https://synthetic.example.invalid/v2"),
    ],
)
def test_model_drift_export_restore_new_process(isolated, key, value):
    root, registry, record, run = isolated
    exported = run("export-config")
    assert exported.returncode == 0
    restore = root / "old-config.json"
    restore.write_text(exported.stdout, encoding="utf-8")
    changed = root / "changed-config.json"
    changed.write_text(json.dumps({key: value}), encoding="utf-8")
    registration = registry.read_bytes()
    assert_safe_failure(run("check", "--config", changed))
    newer = run(
        "snapshot",
        "--version",
        "independent-v2",
        "--source-commit",
        "2" * 40,
        "--effective-at",
        "2026-10-11T01:00:00Z",
        "--rollback",
        "restore previous config",
        "--config",
        changed,
    )
    assert newer.returncode == 0
    assert json.loads(newer.stdout)["model"]["version"] != record["model"]["version"]
    restored = run("check", "--config", restore)
    assert restored.returncode == 0
    assert json.loads(restored.stdout)["model_version"] == record["model"]["version"]
    assert registry.read_bytes() == registration


@pytest.mark.parametrize(
    "raw",
    [
        '{"LLM_TEMPERATURE": NaN}',
        '{"LLM_TIMEOUT": Infinity}',
        '{"LLM_MAX_TOKENS": true}',
        '{"LLM_TIMEOUT": 1e999}',
        "[]",
        '{"LLM_MAX_TOKENS": -1}',
        '{"LLM_TIMEOUT": 0}',
        '{"LLM_DEFAULT_MODLE": "misspelled-model"}',
        '{"LLM_DEFAULT_MODEL":"one","LLM_DEFAULT_MODEL":"two"}',
    ],
)
def test_invalid_configuration_cli_fails_safely(isolated, raw):
    root, registry, _, run = isolated
    config = root / "invalid.json"
    config.write_text(raw, encoding="utf-8")
    original = registry.read_bytes()
    assert_safe_failure(run("check", "--config", config))
    assert registry.read_bytes() == original


@pytest.mark.parametrize(
    "kind",
    [
        "bad-json",
        "wrong-time",
        "date-only-time",
        "duplicate-version",
        "unknown-field",
        "source-traversal",
        "no-current",
    ],
)
def test_malformed_registration_rejected_read_only(isolated, kind):
    _, registry, record, run = isolated
    data = {"schema_version": 1, "current": record["version"], "versions": [record]}
    if kind == "bad-json":
        registry.write_text("{", encoding="utf-8")
    else:
        if kind == "wrong-time":
            record["effective_at"] = "not-a-UTC-time"
        elif kind == "date-only-time":
            record["effective_at"] = "2026-10-11Z"
        elif kind == "duplicate-version":
            data["versions"].append(record.copy())
        elif kind == "unknown-field":
            record["model"]["fields"]["UNKNOWN_KEY"] = "synthetic"
        elif kind == "source-traversal":
            record["sources"]["app/../outside.py"] = "0" * 64
        else:
            data["current"] = "absent-version"
        registry.write_text(json.dumps(data), encoding="utf-8")
    original = registry.read_bytes()
    assert_safe_failure(run("check"))
    assert registry.read_bytes() == original


def test_secret_fields_and_endpoint_credentials_absent_output(isolated):
    root, registry, _, run = isolated
    sentinels = ["SYNTHETIC_API_SECRET", "SYNTHETIC_USER", "SYNTHETIC_PASSWORD", "SYNTHETIC_QUERY_SECRET"]
    config = root / "synthetic-secret-input.json"
    config.write_text(
        json.dumps(
            {
                "LLM_API_KEY": sentinels[0],
                "LLM_BASE_URL": f"https://{sentinels[1]}:{sentinels[2]}@synthetic.invalid/v1?key={sentinels[3]}",
            }
        ),
        encoding="utf-8",
    )
    snapshot = run(
        "snapshot",
        "--version",
        "safe-v1",
        "--source-commit",
        "3" * 40,
        "--effective-at",
        "2026-10-11T02:00:00Z",
        "--rollback",
        "external secret manager",
        "--config",
        config,
    )
    assert snapshot.returncode == 0
    output = snapshot.stdout + snapshot.stderr
    assert all(secret not in output for secret in sentinels)
    record = json.loads(snapshot.stdout)
    registry.write_text(
        json.dumps({"schema_version": 1, "current": record["version"], "versions": [record]}), encoding="utf-8"
    )
    check, exported = run("check", "--config", config), run("export-config")
    assert check.returncode == exported.returncode == 0
    assert all(secret not in check.stdout + check.stderr + exported.stdout + exported.stderr for secret in sentinels)
    # Replay exported nonsecret fields with the separately retained endpoint identity.
    saved_endpoint = json.loads(config.read_text(encoding="utf-8"))["LLM_BASE_URL"]
    restored = json.loads(exported.stdout)
    restored["LLM_BASE_URL"] = saved_endpoint
    config.write_text(json.dumps({**restored, "LLM_DEFAULT_MODEL": "changed-after-release"}), encoding="utf-8")
    assert_safe_failure(run("check", "--config", config))
    config.write_text(json.dumps(restored), encoding="utf-8")
    replay = run("check", "--config", config)
    assert replay.returncode == 0
    assert json.loads(replay.stdout)["model_version"] == record["model"]["version"]
    assert all(secret not in replay.stdout + replay.stderr for secret in sentinels)
