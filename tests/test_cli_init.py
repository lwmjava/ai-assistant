"""初始化配置只操作隔离输出，不回显生成的密钥。"""

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OFFLINE_STARTUP = '''
import socket
_connect = socket.socket.connect
def local_only(self, address):
    if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
        raise AssertionError("external network is disabled")
    return _connect(self, address)
socket.socket.connect = local_only
from pydantic_settings.sources import DotEnvSettingsSource
DotEnvSettingsSource._read_env_files = lambda self: {}
'''


def _environment(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        "ENV": "development", "DATABASE_URL": "sqlite:///" + (tmp_path / "unused.db").as_posix(),
        "INITIAL_ADMIN_USERNAME": "", "INITIAL_ADMIN_PASSWORD": "",
        "INITIAL_SYSTEM_PASSWORD": "", "INITIAL_CULTURE_PASSWORD": "",
    })
    return env


def _run_init(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    script = '''
import runpy
import sys
from pydantic_settings.sources import DotEnvSettingsSource
DotEnvSettingsSource._read_env_files = lambda self: {}
sys.argv = ["ai-assistant", "init", *sys.argv[1:]]
runpy.run_module("app.cli", run_name="__main__")
'''
    return subprocess.run(
        [sys.executable, "-c", script, *args], cwd=tmp_path,
        env=_environment(tmp_path), capture_output=True, text=True, encoding="utf-8", check=False,
    )


def test_cli_init_creates_random_config_without_database_or_secret_output(tmp_path):
    output = tmp_path / "new.env"
    result = _run_init(tmp_path, "--env-file", str(output))
    assert result.returncode == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    key, value = lines[0].split("=", 1)
    assert key == "JWT_SECRET_KEY"
    assert len(value) >= 64
    assert value not in result.stdout + result.stderr
    assert not (tmp_path / "unused.db").exists()
    second_output = tmp_path / "other.env"
    assert _run_init(tmp_path, "--env-file", str(second_output)).returncode == 0
    assert output.read_bytes() != second_output.read_bytes()


def test_cli_init_default_is_current_directory_and_existing_file_is_preserved(tmp_path):
    result = _run_init(tmp_path)
    assert result.returncode == 0
    output = tmp_path / ".env"
    original = output.read_bytes()
    again = _run_init(tmp_path)
    assert again.returncode == 1
    assert output.read_bytes() == original


def test_cli_init_refuses_existing_file_or_directory(tmp_path):
    output = tmp_path / "existing.env"
    original = secrets.token_bytes(32)
    output.write_bytes(original)
    assert _run_init(tmp_path, "--env-file", str(output)).returncode == 1
    assert output.read_bytes() == original
    directory = tmp_path / "directory"
    directory.mkdir()
    assert _run_init(tmp_path, "--env-file", str(directory)).returncode == 1
    assert directory.is_dir()


def test_cli_init_help_does_not_import_settings(tmp_path):
    script = '''
import importlib.abc
import runpy
import sys
class BlockSettings(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app.core.config":
            raise AssertionError("init must not import Settings")
sys.meta_path.insert(0, BlockSettings())
sys.argv = ["ai-assistant", "init", "--help"]
runpy.run_module("app.cli", run_name="__main__")
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=_environment(tmp_path),
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0
    assert "--env-file" in result.stdout


def test_real_module_init_ignores_existing_invalid_environment_file(tmp_path):
    # 二进制哨兵放在临时 cwd；初始化不得尝试读取任何配置文件。
    (tmp_path / ".env").write_bytes(b"\xff\xfe\x00")
    output = tmp_path / "fresh.env"
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "init", "--env-file", str(output)],
        cwd=tmp_path, env=_environment(tmp_path), capture_output=True,
        text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0
    assert output.read_text(encoding="utf-8").startswith("JWT_SECRET_KEY=")


def test_init_execution_does_not_import_settings_or_database(tmp_path):
    script = '''
import importlib.abc
import runpy
import sys
class BlockApplication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"app.core.config", "app.core.database", "pydantic_settings"}:
            raise AssertionError("init imported runtime configuration")
sys.meta_path.insert(0, BlockApplication())
sys.argv = ["ai-assistant", "init", "--env-file", sys.argv[1]]
runpy.run_module("app.cli", run_name="__main__")
'''
    output = tmp_path / "blocked-import.env"
    result = subprocess.run(
        [sys.executable, "-c", script, str(output)], cwd=tmp_path, env=_environment(tmp_path),
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0
    assert output.exists()


def test_failed_write_removes_only_new_file(tmp_path, monkeypatch):
    from app.core import bootstrap_config

    output = tmp_path / "failed.env"

    def fail_sync(_descriptor):
        raise OSError("synthetic write failure")

    monkeypatch.setattr(bootstrap_config.os, "fsync", fail_sync)
    with pytest.raises(OSError):
        bootstrap_config.initialize_config(output)
    assert not output.exists()


def test_init_refuses_symlink_without_touching_target(tmp_path):
    from app.core.bootstrap_config import initialize_config

    target = tmp_path / "original"
    original = secrets.token_bytes(16)
    target.write_bytes(original)
    link = tmp_path / "linked.env"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("操作系统未授予符号链接创建能力，Linux 门禁仍需验证")
    with pytest.raises(FileExistsError):
        initialize_config(link)
    assert target.read_bytes() == original
    assert link.is_symlink()


@pytest.mark.skipif(os.name != "posix", reason="0600 权限语义必须在 Linux 实跑")
def test_init_file_permissions_are_private(tmp_path):
    from app.core.bootstrap_config import initialize_config

    output = tmp_path / "private.env"
    initialize_config(output)
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("environment", ["production", "staging"])
@pytest.mark.parametrize("key", ["", "   ", "change-me-in-production-use-a-random-secret"])
def test_non_development_unsafe_secret_refuses_before_schema(tmp_path, environment, key):
    import sqlite3

    script = OFFLINE_STARTUP + '''
from fastapi.testclient import TestClient
from app.main import app
try:
    with TestClient(app):
        pass
except RuntimeError as exc:
    assert "ai-assistant init" in str(exc)
    print("REFUSED before startup")
else:
    raise AssertionError("unsafe startup was accepted")
'''
    env = _environment(tmp_path)
    env.update({"ENV": environment, "JWT_SECRET_KEY": key})
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0
    assert "REFUSED before startup" in result.stdout
    db = tmp_path / "unused.db"
    if db.exists():
        with sqlite3.connect(db) as connection:
            assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_generated_secret_allows_isolated_startup(tmp_path):
    output = tmp_path / "valid.env"
    assert _run_init(tmp_path, "--env-file", str(output)).returncode == 0
    env = _environment(tmp_path)
    env["ENV"] = "staging"
    env["JWT_SECRET_KEY"] = output.read_text(encoding="utf-8").strip().split("=", 1)[1]
    script = OFFLINE_STARTUP + '''
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app):
    print("STARTED with generated configuration")
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0
    assert "STARTED with generated configuration" in result.stdout
    assert env["JWT_SECRET_KEY"] not in result.stdout + result.stderr
