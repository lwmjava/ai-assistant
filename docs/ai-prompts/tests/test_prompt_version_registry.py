import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "app/agents/version_registry.py"
spec = importlib.util.spec_from_file_location("offline_registry", CLI)
assert spec and spec.loader
registry_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry_api)


def run(root, command="check", *args):
    return subprocess.run(
        [sys.executable, str(CLI), command, "--root", str(root), *args], capture_output=True, text=True, check=False
    )


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "app/agents").mkdir(parents=True)
    (tmp_path / "app/core").mkdir()
    (tmp_path / "docs/ai-prompts").mkdir(parents=True)
    shutil.copyfile(ROOT / "app/core/config.py", tmp_path / "app/core/config.py")
    (tmp_path / "app/agents/prompts.py").write_text('PROMPT = "original"\n')
    record = registry_api.make_record(
        tmp_path,
        version="v1",
        source_commit="a" * 40,
        effective_at="2026-10-11T00:00:00Z",
        rollback="restore previous source and config",
    )
    path = tmp_path / registry_api.REGISTRY
    path.write_text(json.dumps({"schema_version": 1, "current": "v1", "versions": [record]}))
    return tmp_path


def test_existing_prompts_have_version_registration():
    assert (ROOT / "app/agents/prompts.py").is_file()
    assert (
        ROOT / registry_api.REGISTRY
    ).is_file(), "Existing prompt source has no version/source/effective-time/rollback registry"
    assert run(ROOT).returncode == 0


def test_source_rollback_new_process_and_read_only(tree):
    original = (tree / "app/agents/prompts.py").read_bytes()
    metadata = (tree / registry_api.REGISTRY).read_bytes()
    assert run(tree).returncode == 0
    (tree / "app/agents/prompts.py").write_text('PROMPT="changed"')
    assert run(tree).returncode == 1
    (tree / "app/agents/prompts.py").write_bytes(original)
    assert run(tree).returncode == run(tree).returncode == 0
    assert (tree / registry_api.REGISTRY).read_bytes() == metadata


@pytest.mark.parametrize("action", ["new", "delete", "newline"])
def test_source_inventory_boundaries(tree, action):
    source = tree / "app/agents/prompts.py"
    if action == "new":
        (tree / "app/agents/new_prompt.py").write_text('P="new"')
    elif action == "delete":
        source.unlink()
    else:
        source.write_bytes(source.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert run(tree).returncode == (0 if action == "newline" else 1)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 1e309])
def test_nonfinite_model_rejected(tree, value):
    reference = registry_api.defaults(tree)
    with pytest.raises(ValueError):
        registry_api.model_snapshot({"LLM_TEMPERATURE": value}, reference)


def test_model_change_and_recoverable_snapshot(tree):
    reference = registry_api.defaults(tree)
    original = registry_api.model_snapshot({}, reference)
    changed = registry_api.model_snapshot({"LLM_DEFAULT_MODEL": "other-model"}, reference)
    assert changed["version"] != original["version"]
    export = run(tree, "export-config")
    assert export.returncode == 0
    config = tree / "safe-config.json"
    config.write_text(export.stdout)
    assert run(tree, "check", "--config", str(config)).returncode == 0
    config.write_text(json.dumps({**json.loads(export.stdout), "LLM_DEFAULT_MODEL": "other-model"}))
    assert run(tree, "check", "--config", str(config)).returncode == 1
    config.write_text(export.stdout)
    assert run(tree, "check", "--config", str(config)).returncode == 0


def test_secrets_omitted_endpoint_identity_changes(tree):
    defaults = registry_api.defaults(tree)
    secret = "synthetic-private-marker"
    snap = registry_api.model_snapshot(
        {"LLM_API_KEY": secret, "DATABASE_URL": secret, "LLM_BASE_URL": "https://internal.example/" + secret}, defaults
    )
    assert secret not in json.dumps(snap)
    assert snap["version"] != registry_api.model_snapshot({}, defaults)["version"]


@pytest.mark.parametrize("bad", ["{", '{"schema_version":1,"schema_version":1}', '{"schema_version":NaN}'])
def test_invalid_metadata_fails_without_write(tree, bad):
    path = tree / registry_api.REGISTRY
    path.write_text(bad)
    result = run(tree)
    assert result.returncode == 1
    assert path.read_text() == bad
    assert "Traceback" not in result.stderr


def test_snapshot_strict_schema_and_duplicate_versions(tree):
    path = tree / registry_api.REGISTRY
    data = json.loads(path.read_text())
    data["versions"].append(data["versions"][0])
    path.write_text(json.dumps(data))
    assert run(tree).returncode == 1


def test_cli_no_dotenv_read_and_cwd_independent(tree):
    # dotenv cannot be read as text; direct CLI must not touch it.
    (tree / ".env").mkdir()
    result = subprocess.run(
        [sys.executable, str(CLI), "check", "--root", str(tree)],
        cwd=tree / "app",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0


@pytest.mark.parametrize("bad", [[], 1, "private-marker"])
def test_cli_rejects_nonobject_config_without_traceback(tree, bad):
    path = tree / "input.json"
    path.write_text(json.dumps(bad))
    result = run(tree, "check", "--config", str(path))
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "private-marker" not in result.stderr


@pytest.mark.parametrize(
    "overrides", [{"LLM_TIMEOUT": -1}, {"LLM_MAX_TOKENS": 0}, {"EMBEDDING_DIM": 0}, {"unexpected": 1}]
)
def test_strict_numeric_and_unknown_field_boundary(tree, overrides):
    with pytest.raises(ValueError):
        registry_api.model_snapshot(overrides, registry_api.defaults(tree))


def test_capability_source_only_hashed_and_numeric_canonical(tree):
    reference = registry_api.defaults(tree)
    config = {"LLM_CAPABILITY_DECLARED_SOURCE": "https://user:private-marker@example.invalid/"}
    assert "private-marker" not in json.dumps(registry_api.model_snapshot(config, reference))
    assert registry_api.model_snapshot({"LLM_TIMEOUT": 1}, reference) == registry_api.model_snapshot(
        {"LLM_TIMEOUT": 1.0}, reference
    )


def test_effective_timestamp_requires_time(tree):
    with pytest.raises(ValueError):
        registry_api.make_record(
            tree, version="v2", source_commit="a" * 40, effective_at="2026-10-11Z", rollback="restore"
        )


@pytest.mark.parametrize("timestamp", ["2026-10-11T00:00:00+08:00Z", "2026-10-11Z"])
def test_rejects_false_utc_timestamp(tree, timestamp):
    with pytest.raises(ValueError):
        registry_api.make_record(tree, version="v2", source_commit="a" * 40, effective_at=timestamp, rollback="restore")


def test_git_source_baseline_must_exist():
    with pytest.raises(ValueError):
        registry_api.make_record(
            ROOT, version="v2", source_commit="0" * 40, effective_at="2026-10-11T00:00:00Z", rollback="restore"
        )


def test_ast_missing_default_fails_explicitly(tree):
    source = tree / "app/core/config.py"
    text = source.read_text(encoding="utf-8")
    source.write_text(text.replace('LLM_PROVIDER: str = "openai"', "LLM_PROVIDER: str"), encoding="utf-8")
    with pytest.raises(ValueError, match="default missing"):
        registry_api.defaults(tree)


def test_config_guard_before_ast_read(tree, monkeypatch):
    actual = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == "config.py" or actual(path))
    monkeypatch.setattr(
        Path, "read_text", lambda *args, **kwargs: pytest.fail("read attempted before source identity validation")
    )
    with pytest.raises(ValueError, match="outside repository"):
        registry_api.defaults(tree)
