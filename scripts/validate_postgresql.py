"""Validate real PostgreSQL using an owned disposable Docker instance without user data volumes."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import secrets
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_delivery import input_snapshot  # noqa: E402


class ValidationError(RuntimeError):
    """A public-safe validation failure code."""


def official_image(value: str) -> str:
    if not re.fullmatch(r"postgres:(?:14|15|16|17)(?:\.\d+)?(?:-alpine)?", value):
        raise argparse.ArgumentTypeError("an explicit supported official PostgreSQL image tag is required")
    return value


def docker(*args: str, timeout: int = 60) -> str:
    result = subprocess.run(["docker", *args], cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise ValidationError("docker_command_failed")
    return result.stdout.strip()


def require_local_daemon(endpoint: str) -> None:
    try:
        parsed = urlsplit(endpoint)
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError
        if parsed.scheme == "tcp":
            valid = (parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port is not None
                     and parsed.port > 0 and parsed.path in {"", "/"})
        elif parsed.scheme == "unix":
            valid = not parsed.netloc and parsed.path.startswith("/")
        elif parsed.scheme == "npipe":
            valid = (not parsed.netloc and parsed.path.startswith("//./pipe/")) or (
                parsed.netloc == "." and parsed.path.startswith("/pipe/"))
        else:
            valid = False
        if not valid:
            raise ValueError
    except ValueError:
        raise ValidationError("remote_docker_daemon_not_allowed") from None


def cleanup_container(name: str, run_id: str) -> None:
    names = docker("ps", "--all", "--filter", f"label=ai-assistant.validation.run={run_id}", "--format", "{{.Names}}")
    if not names:
        return
    if names.splitlines() != [name]:
        raise ValidationError("container_ownership_mismatch")
    docker("rm", "--force", name)


def junit_result(path: Path) -> dict[str, int]:
    if not path.is_file():
        raise ValidationError("pytest_report_missing")
    cases = list(ET.parse(path).getroot().iter("testcase"))
    if not cases:
        raise ValidationError("no_postgresql_cases_collected")
    counts = {"tests": len(cases), "failures": 0, "errors": 0, "skipped": 0}
    for case in cases:
        for key, tag in (("failures", "failure"), ("errors", "error"), ("skipped", "skipped")):
            counts[key] += int(case.find(tag) is not None)
    return counts


def validate(run: Path, image: str) -> dict:
    import psycopg2
    from sqlalchemy.engine import URL

    run_id = run.name
    name = "nfr005-" + secrets.token_hex(8)
    database = "validation_" + secrets.token_hex(8)
    password = secrets.token_urlsafe(24) + "@%/"
    env_file = run / "postgres.env"
    result: dict = {
        "schema_version": "postgresql-validation-1.0.0", "status": "running",
        "started_at": datetime.now(UTC).isoformat(), "image": image,
        "isolation": "owned loopback-only container, tmpfs data, no user volumes",
        "inputs": input_snapshot(ROOT, ["backend"]),
        "interpreter": sys.executable, "python": sys.version.split()[0],
        "packages": {package: importlib.metadata.version(package)
                     for package in ("psycopg2-binary", "sqlalchemy", "sqlmodel", "alembic", "pytest")},
        "commands": [["python", "-m", "pytest", "tests/test_postgresql_integration.py"]],
    }
    creation_attempted = False
    try:
        endpoint = (os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None) or docker(
            "context", "inspect", "--format", '{{ (index .Endpoints "docker").Host }}',
        )
        require_local_daemon(endpoint)
        result["image_id"] = docker("image", "inspect", "--format", "{{.Id}}", image)
        result["docker_server"] = docker("version", "--format", "{{.Server.Version}}")
        env_file.write_text(f"POSTGRES_USER=validation\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB={database}\n",
                            encoding="utf-8")
        creation_attempted = True
        docker("run", "--detach", "--rm", "--pull=never", "--name", name,
               "--label", "ai-assistant.validation=nfr005", "--label", f"ai-assistant.validation.run={run_id}",
               "--tmpfs", "/var/lib/postgresql/data:rw", "--publish", "127.0.0.1::5432",
               "--env-file", str(env_file), image)
        address = docker("port", name, "5432/tcp")
        if not re.fullmatch(r"127\.0\.0\.1:\d+", address):
            raise ValidationError("container_port_not_loopback")
        port = int(address.rsplit(":", 1)[1])
        for _ in range(45):
            try:
                with psycopg2.connect(host="127.0.0.1", port=port, user="validation", password=password,
                                      dbname=database, connect_timeout=1) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SHOW server_version")
                        result["server_version"] = cursor.fetchone()[0]
                        cursor.execute("SHOW timezone")
                        result["server_timezone"] = cursor.fetchone()[0]
                break
            except psycopg2.OperationalError:
                time.sleep(1)
        else:
            raise ValidationError("postgresql_not_ready")
        env = os.environ.copy()
        env.update({
            "POSTGRES_VALIDATION_URL": URL.create("postgresql+psycopg2", username="validation",
                password=password, host="127.0.0.1", port=port, database=database
            ).render_as_string(hide_password=False),
            "POSTGRES_VALIDATION_REQUIRED": "1",
            "DATABASE_URL": "sqlite:///" + (run / "guard.sqlite").as_posix(),
        })
        command = [sys.executable, "-m", "pytest", "tests/test_postgresql_integration.py", "-v",
                   "--basetemp", str(run / "tmp"), "-o", "cache_dir=" + str(run / "pytest-cache"),
                   "--junitxml", str(run / "pytest.xml")]
        completed = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=600)
        output = (completed.stdout + completed.stderr).replace(password, "[REDACTED]")
        (run / "pytest.log").write_text(output, encoding="utf-8")
        result["exit_code"] = completed.returncode
        result["counts"] = junit_result(run / "pytest.xml")
        result["status"] = "passed" if completed.returncode == 0 and not any(
            result["counts"][key] for key in ("failures", "errors", "skipped")) else "failed"
        if result["inputs"] != input_snapshot(ROOT, ["backend"]):
            result["status"] = "invalidated"
    except Exception as exc:
        result["status"] = "infrastructure_error"
        result["error"] = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
    finally:
        try:
            if creation_attempted:
                cleanup_container(name, run_id)
            result["cleanup"] = "complete"
        except Exception as exc:
            result["cleanup"] = "failed"
            result["status"] = "infrastructure_error"
            result["cleanup_error"] = type(exc).__name__
        env_file.unlink(missing_ok=True)
        result["finished_at"] = datetime.now(UTC).isoformat()
        (run / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=official_image, default="postgres:16-alpine")
    args = parser.parse_args(argv)
    run = ROOT / "data" / "validation" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-postgres-"
                                        + secrets.token_hex(6))
    run.mkdir(parents=True)
    try:
        result = validate(run, args.image)
    except Exception as exc:
        result = {"status": "infrastructure_error", "error": type(exc).__name__}
        (run / "result.json").write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps({"run": str(run), "status": result["status"], "counts": result.get("counts"),
                      "error": result.get("error")}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
