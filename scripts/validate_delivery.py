"""Run isolated checks and retain evidence bound to the checked inputs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import tomllib
from contextlib import nullcontext
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL_KEYS = (
    "LLM_API_KEY", "LLM_INTENT_API_KEY", "LLM_FALLBACK_API_KEY", "LLM_RERANK_API_KEY", "EMBEDDING_API_KEY",
)
CHECK_PACKAGES = ("pytest", "ruff", "mypy", "sqlmodel")
TEXT_SUFFIXES = {".py", ".tsx", ".ts", ".json", ".yaml", ".yml", ".toml", ".md", ".css", ".html"}
FRONTEND_SUFFIXES = TEXT_SUFFIXES | {".svg", ".png", ".ico"}
PERMISSION_FLOOD_THRESHOLD = 5


def create_run(root: Path) -> Path:
    parent = root.resolve() / "data" / "validation"
    if not parent.resolve().is_relative_to(root.resolve()):
        raise ValueError("validation directory resolves outside the workspace")
    parent.mkdir(parents=True, exist_ok=True)
    run = parent / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:12])
    run.mkdir()
    return run


def checked_run(root: Path, run: Path) -> Path:
    root, run = root.resolve(), run.resolve()
    parent = root / "data" / "validation"
    if not run.is_relative_to(parent) or run == parent or not run.is_dir():
        raise ValueError("run must be an existing directory inside data/validation")
    return run


def select_tests(root: Path, selections: list[str]) -> list[str]:
    if not selections:
        raise ValueError("targeted checks require an explicit --test selection")
    selected = []
    root = root.resolve()
    for selection in selections:
        name = selection.split("::", 1)[0]
        candidate = (root / name).resolve()
        if (
            name.startswith("-") or Path(name).is_absolute() or ".." in Path(name).parts
            or not candidate.is_relative_to(root / "tests") or candidate.suffix != ".py"
            or not candidate.is_file()
        ):
            raise ValueError("test selection must identify an existing Python file under tests/")
        if selection not in selected:
            selected.append(selection)
    return selected


def check_environment(run: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({key: "" for key in CREDENTIAL_KEYS})
    env.update({
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "ENV": "development",
        "DATABASE_URL": "sqlite:///" + (run / "pytest.db").resolve().as_posix(),
        "AUTH_ENABLED": "true", "AGENT_ORCHESTRATION": "self", "SECURITY_RATE_LIMIT": "false",
        "INITIAL_ADMIN_USERNAME": "", "INITIAL_ADMIN_PASSWORD": "",
        "RUFF_CACHE_DIR": str(run / "ruff-cache"), "MYPY_CACHE_DIR": str(run / "mypy-cache"),
    })
    return env


def preflight(root: Path, run: Path) -> dict:
    run = checked_run(root, run)
    for module in ("pytest", "ruff", "mypy", "sqlmodel"):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError("missing check dependency: " + module)
    probe = run / ("probe-" + uuid4().hex)
    renamed = probe.with_suffix(".checked")
    try:
        probe.write_text("isolated", encoding="utf-8")
        probe.rename(renamed)
        if renamed.read_text(encoding="utf-8") != "isolated":
            raise OSError("validation directory is not readable")
    finally:
        probe.unlink(missing_ok=True)
        renamed.unlink(missing_ok=True)
    database = run / ("probe-" + uuid4().hex + ".db")
    try:
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TABLE probe (value INTEGER)")
            connection.execute("INSERT INTO probe VALUES (1)")
            connection.commit()
            if connection.execute("SELECT value FROM probe").fetchone() != (1,):
                raise OSError("test database is not readable")
        finally:
            connection.close()
    finally:
        database.unlink(missing_ok=True)
    return {
        "interpreter": sys.executable,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "database_url": check_environment(run)["DATABASE_URL"],
        "packages": package_versions(),
        "declared_extras": declared_extras(root),
        "revision": source_revision(root),
    }


def package_versions() -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for name in CHECK_PACKAGES:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = None
    return found


def declared_extras(root: Path) -> list[str]:
    document = root / "pyproject.toml"
    if not document.is_file():
        return []
    try:
        loaded = tomllib.loads(document.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    extras = loaded.get("project", {}).get("optional-dependencies", {})
    if not isinstance(extras, dict):
        return []
    return sorted(str(name) for name in extras)


def source_revision(root: Path) -> dict[str, str | bool | None]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return {"head": None, "dirty": None}
    return {"head": head or None, "dirty": dirty}


def process_exists(pid: object) -> bool | None:
    """Return whether a pid is alive. None means the check cannot confirm either way."""
    if type(pid) is not int or pid <= 0:
        return None
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
        kernel.GetExitCodeProcess.restype = ctypes.c_int
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            return None
        code = ctypes.c_uint32()
        known = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        if not known:
            return None
        return code.value == 259
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def input_snapshot(root: Path, scopes: list[str]) -> dict[str, str]:
    root = root.resolve()
    common = ["scripts", ".github/workflows", "docs/ai-prompts"]
    paths = set(root.glob("requirements*.txt")) | {root / "pyproject.toml"}
    directories = list(common)
    if "backend" in scopes:
        directories += ["app", "tests", "alembic", "evals/datasets", "evals/thresholds"]
        docs = root / "docs"
        if docs.exists():
            paths.update(path for path in docs.rglob("*.py") if path.is_file() and "__pycache__" not in path.parts)
    if "frontend" in scopes:
        directories += ["frontend/src", "app/schemas", "app/api/routes"]
        paths.update(p for p in (root / "frontend").glob("*")
                     if p.is_file() and not p.name.startswith(".env"))
    for directory in directories:
        base = root / directory
        suffixes = FRONTEND_SUFFIXES if directory.startswith("frontend") else TEXT_SUFFIXES
        if base.exists():
            paths.update(p for p in base.rglob("*") if p.is_file()
                         and "__pycache__" not in p.parts and p.suffix in suffixes)
    result = {}
    for path in sorted(paths):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError("check input resolves outside the workspace")
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class LockHeldError(Exception):
    """A full check already owns this workspace. The existing lock is left untouched."""

    def __init__(self, path: Path, occupant: dict):
        self.occupant = {
            "pid": occupant.get("pid"),
            "run_id": occupant.get("run_id"),
            "started_at": occupant.get("started_at"),
            "path": str(path),
        }
        super().__init__(
            "已有全量进行中："
            f"pid={self.occupant['pid']} run_id={self.occupant['run_id']} path={path}"
        )


class WorkspaceLock:
    """Exclude concurrent full checks; stale locks need explicit investigation."""

    def __init__(self, root: Path, run: Path):
        self.path = root.resolve() / "data/validation/full.lock"
        self.record = {"pid": os.getpid(), "run_id": run.name, "token": uuid4().hex,
                       "started_at": datetime.now(UTC).isoformat()}

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise LockHeldError(self.path, read_lock(self.path)) from None
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(self.record, stream)
        return self

    def __exit__(self, *_):
        try:
            if json.loads(self.path.read_text(encoding="utf-8")).get("token") == self.record["token"]:
                self.path.unlink()
        except (OSError, ValueError):
            pass


def read_lock(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def permission_flood(log: Path) -> str | None:
    """Detect the same workspace permission failure repeated in a live log."""
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    matches = [
        line.strip() for line in lines
        if "PermissionError" in line and ("WinError 5" in line or "pytest-current" in line or "pytest-of-" in line)
    ]
    if len(matches) < PERMISSION_FLOOD_THRESHOLD:
        return None
    return matches[0][:500]


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)


def run_command(command: list[str], cwd: Path, log: Path, environment: dict[str, str], timeout: float = 1200) -> dict:
    started = time.monotonic()
    deadline = started + timeout
    status = "failed"
    code = None
    sample = None
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=os.name != "nt",
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    sample = permission_flood(log)
                    status = "infrastructure_error" if sample is not None else "timeout"
                    stop_process(process)
                    code = process.returncode
                    break
                try:
                    code = process.wait(timeout=min(0.2, remaining))
                except subprocess.TimeoutExpired:
                    sample = permission_flood(log)
                    if sample is None:
                        continue
                    status = "infrastructure_error"
                    stop_process(process)
                    code = process.returncode
                    break
                sample = permission_flood(log)
                status = "infrastructure_error" if sample is not None else ("passed" if code == 0 else "failed")
                break
        except KeyboardInterrupt:
            status = "cancelled"
            stop_process(process)
            code = process.returncode
    outcome = {"command": command, "exit_code": code, "status": status,
               "elapsed_seconds": round(time.monotonic() - started, 3), "log": log.name}
    if sample is not None:
        outcome["sample"] = sample
    return outcome


def record_outcome(result: dict, outcome: dict, current_inputs: dict) -> None:
    result["commands"].append(outcome)
    if outcome.get("sample"):
        result["sample"] = outcome["sample"]
    if current_inputs != result.get("inputs"):
        result["status"] = "invalidated"
    elif outcome["status"] != "passed":
        result["status"] = outcome["status"]


def save_result(run: Path, result: dict) -> None:
    temporary = run / "result.pending"
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(run / "result.json")


def summarize(root: Path, run: Path) -> dict:
    run = checked_run(root, run)
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    persist = False
    if result.get("status") == "passed" and result.get("inputs") != input_snapshot(root, result.get("scopes", [])):
        result["status"] = "invalidated"
        result["reason"] = "checked inputs changed after the run"
        persist = True
    elif result.get("status") == "running":
        alive = process_exists(result.get("pid"))
        if alive is False:
            result["status"] = "interrupted"
            result["recovery"] = (
                "记录中的进程已结束。若 data/validation/full.lock 仍在，确认其命令行后手动删除；不要结束无关进程。"
            )
            persist = True
        else:
            result["recovery"] = (
                "无法确认记录中的进程已结束。请先核对进程命令行；"
                "不要仅凭 PID 结束进程或删除 data/validation/full.lock。"
            )
    if persist:
        save_result(run, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["preflight", "targeted", "full", "summary"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--test", action="append", default=[])
    parser.add_argument("--frontend", action="store_true")
    parser.add_argument("--run", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.mode == "summary":
        if args.run is None:
            parser.error("summary requires --run")
        try:
            result = summarize(root, args.run)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") == "passed" else 1
    if args.mode == "targeted" and not args.test and not args.frontend:
        parser.error("targeted requires --test or --frontend")
    try:
        tests = select_tests(root, args.test) if args.test else []
    except ValueError as exc:
        parser.error(str(exc))
    run = create_run(root)
    scopes = ["backend"] if args.mode == "full" or tests else []
    if args.frontend:
        scopes.append("frontend")
    result = {"mode": args.mode, "run_id": run.name, "pid": os.getpid(), "status": "running", "commands": [],
              "scopes": scopes, "started_at": datetime.now(UTC).isoformat(), "inputs": input_snapshot(root, scopes)}
    save_result(run, result)
    env = check_environment(run)
    try:
        result["environment"] = preflight(root, run)
        commands = []
        if args.mode == "full" or tests:
            command = [sys.executable, "-m", "pytest", *tests,
                       "--basetemp", str(run / "tmp"), "-o", "cache_dir=" + str(run / "pytest-cache"),
                       "--junitxml", str(run / "pytest.xml")]
            collection = [sys.executable, "-m", "pytest", *tests, "--collect-only", "-q",
                          "--basetemp", str(run / "collection-tmp"), "-o", "cache_dir=" + str(run / "collection-cache")]
            commands.append(("collection", collection, root))
            commands.append(("pytest", command, root))
        if args.mode == "full":
            commands.extend([("ruff", [sys.executable, "-m", "ruff", "check", "."], root),
                             ("mypy", [sys.executable, "-m", "mypy", "app/"], root)])
        if args.frontend:
            npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
            if npm is None:
                raise RuntimeError("npm is unavailable")
            commands.extend(("frontend-" + name, [npm, "run", name], root / "frontend")
                            for name in ["typecheck", "test", "lint", "build"])
        guard = WorkspaceLock(root, run) if args.mode == "full" else nullcontext()
        with guard:
            for name, command, cwd in commands:
                print(json.dumps({"run": str(run), "check": name, "status": "running"}), flush=True)
                outcome = run_command(command, cwd, run / (name + ".log"), env)
                record_outcome(result, outcome, input_snapshot(root, scopes))
                save_result(run, result)
                if result["status"] != "running":
                    break
            else:
                result["status"] = "passed"
    except LockHeldError as exc:
        result["status"] = "lock_held"
        result["occupant"] = exc.occupant
        result["reason"] = str(exc)
    except ValueError as exc:
        result["status"] = "rejected"
        result["reason"] = str(exc)
    except (OSError, RuntimeError) as exc:
        result["status"] = "infrastructure_error"
        result["error_type"] = type(exc).__name__
        result["reason"] = str(exc)
    except KeyboardInterrupt:
        result["status"] = "cancelled"
    result["finished_at"] = datetime.now(UTC).isoformat()
    save_result(run, result)
    published = {"run": str(run), "status": result["status"]}
    if result.get("reason"):
        published["reason"] = result["reason"]
    if result.get("occupant"):
        published["occupant"] = result["occupant"]
    print(json.dumps(published, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
