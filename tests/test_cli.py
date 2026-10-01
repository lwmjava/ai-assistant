"""命令行迁移与创建系统管理员。子进程使用临时 SQLite，不碰开发库。"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "sentry-cli-password-9f3a"

START_SCRIPT = """
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    client.get("/api/health")
print("STARTED yes")
"""

LOGIN_SCRIPT = """
import os
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    response = client.post(
        "/api/auth/login",
        json={
            "username": os.environ["CLI_LOGIN_USERNAME"],
            "password": os.environ["INITIAL_ADMIN_PASSWORD"],
        },
    )
token = ""
if response.status_code == 200:
    token = response.json().get("access_token") or ""
print("STATUS", response.status_code)
print("HAS_TOKEN", "yes" if token else "no")
"""


def _env(db: Path, password: str) -> dict[str, str]:
    env = os.environ.copy()
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), previous]) if previous else str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["DATABASE_URL"] = "sqlite:///" + db.resolve().as_posix()
    env["ENV"] = "development"
    env["DB_ECHO"] = "false"
    env["INITIAL_ADMIN_USERNAME"] = ""
    env["INITIAL_ADMIN_PASSWORD"] = password
    return env


def _run(args: list[str], db: Path, password: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        cwd=cwd,
        env=_env(db, password),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _run_script(script: str, db: Path, password: str, cwd: Path, extra: dict[str, str] | None = None):
    env = _env(db, password)
    if extra:
        env.update(extra)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _assert_no_secret(result: subprocess.CompletedProcess[str], secret: str) -> None:
    assert secret not in (result.stdout or "")
    assert secret not in (result.stderr or "")


def _alembic_version(db: Path) -> str | None:
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "alembic_version" not in tables:
            return None
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    if row is None:
        return None
    return str(row[0])


def _count(db: Path, sql: str) -> int:
    if not db.exists():
        return 0
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "users" not in tables:
            return 0
        row = conn.execute(sql).fetchone()
    return int(row[0]) if row is not None else 0


def test_migrate_empty_database_then_reports_current(tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    first = _run(["migrate", "--yes"], db, SENTINEL, tmp_path)
    assert first.returncode == 0, first.stderr
    _assert_no_secret(first, SENTINEL)
    assert _alembic_version(db)

    started = _run_script(START_SCRIPT, db, SENTINEL, tmp_path)
    assert started.returncode == 0, started.stderr
    assert "STARTED yes" in started.stdout
    _assert_no_secret(started, SENTINEL)

    second = _run(["migrate", "--yes"], db, SENTINEL, tmp_path)
    assert second.returncode == 0, second.stderr
    assert "up to date" in second.stdout.lower()
    _assert_no_secret(second, SENTINEL)


def test_module_migration_entry_still_runs(tmp_path: Path) -> None:
    db = tmp_path / "module.db"
    result = subprocess.run(
        [sys.executable, "-m", "app.core.migration", "migrate", "--yes"],
        cwd=tmp_path,
        env=_env(db, SENTINEL),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert _alembic_version(db)
    _assert_no_secret(result, SENTINEL)


def test_create_superuser_can_login_and_duplicate_is_rejected(tmp_path: Path) -> None:
    db = tmp_path / "admin.db"
    migrated = _run(["migrate", "--yes"], db, SENTINEL, tmp_path)
    assert migrated.returncode == 0, migrated.stderr

    created = _run(
        ["admin", "create-superuser", "--username", "cli-admin"],
        db,
        SENTINEL,
        tmp_path,
    )
    assert created.returncode == 0, created.stderr
    assert "cli-admin" in created.stdout
    assert "cli-admin" in created.stderr
    _assert_no_secret(created, SENTINEL)
    assert _count(db, "SELECT COUNT(*) FROM users") == 1
    assert _count(db, "SELECT COUNT(*) FROM users WHERE role = 'system_admin'") == 1

    login = _run_script(
        LOGIN_SCRIPT,
        db,
        SENTINEL,
        tmp_path,
        extra={"CLI_LOGIN_USERNAME": "cli-admin"},
    )
    assert login.returncode == 0, login.stderr
    assert "STATUS 200" in login.stdout
    assert "HAS_TOKEN yes" in login.stdout
    _assert_no_secret(login, SENTINEL)

    again = _run(
        ["admin", "create-superuser", "--username", "cli-admin-2"],
        db,
        SENTINEL,
        tmp_path,
    )
    assert again.returncode != 0
    assert "已有系统管理员" in again.stderr
    _assert_no_secret(again, SENTINEL)
    assert _count(db, "SELECT COUNT(*) FROM users") == 1


def test_short_password_writes_no_user(tmp_path: Path) -> None:
    db = tmp_path / "short.db"
    short = "shortpw"
    migrated = _run(["migrate", "--yes"], db, short, tmp_path)
    assert migrated.returncode == 0, migrated.stderr
    result = _run(
        ["admin", "create-superuser", "--username", "cli-admin"],
        db,
        short,
        tmp_path,
    )
    assert result.returncode != 0
    assert "密码至少需要 8 位" in result.stderr
    _assert_no_secret(result, short)
    assert _count(db, "SELECT COUNT(*) FROM users") == 0


def test_concurrent_create_allows_one(tmp_path: Path) -> None:
    db = tmp_path / "concurrent.db"
    migrated = _run(["migrate", "--yes"], db, SENTINEL, tmp_path)
    assert migrated.returncode == 0, migrated.stderr

    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "app.cli", "admin", "create-superuser", "--username", name],
            cwd=tmp_path,
            env=_env(db, SENTINEL),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for name in ("cli-admin-a", "cli-admin-b")
    ]
    outputs: list[tuple[int, str, str]] = []
    for proc in processes:
        stdout, stderr = proc.communicate(timeout=120)
        outputs.append((proc.returncode if proc.returncode is not None else 1, stdout, stderr))

    combined = "\n".join(item for _code, stdout, stderr in outputs for item in (stdout, stderr))
    assert SENTINEL not in combined
    assert sum(code == 0 for code, _stdout, _stderr in outputs) == 1
    assert _count(db, "SELECT COUNT(*) FROM users WHERE role = 'system_admin'") == 1


def test_console_script_points_at_cli() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'ai-assistant = "app.cli:main"' in text
