"""Backup and restore PostgreSQL, knowledge uploads, and Milvus volumes.

The default production layout is a Compose project with a PostgreSQL
service named db, knowledge files at /app/data/knowledge on the appdata
volume, and Milvus state in the milvusdata, etcddata, and miniodata
volumes. This tool writes a timestamped backup directory and a manifest
with checksums. It does not print credentials, connection strings, or
file contents. It does not configure offsite copies, encryption, or
schedulers.

Restore requires an explicit isolated target and confirmation. Known
production project names are refused. The worktree knowledge directory
is never used as a restore destination.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SHANGHAI = timezone(timedelta(hours=8))
SCHEMA_VERSION = "backup-1.0.0"
ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.prod.yml"
KNOWLEDGE_REL = Path("data") / "knowledge"
WORKTREE_KNOWLEDGE = (ROOT / KNOWLEDGE_REL).resolve()

PRODUCTION_PROJECTS = frozenset(
    {
        "assistant",
        "production",
        "prod",
        "ai-assistant",
        "aiassistant",
        "aigates",
    }
)

MILVUS_VOLUMES = ("milvusdata", "etcddata", "miniodata")
TAR_IMAGE_CANDIDATES = ("postgres:16-alpine", "alpine:3.20", "alpine:3.19", "alpine")


class BackupError(RuntimeError):
    """Stable, user-safe failure code."""


def shanghai_now() -> datetime:
    return datetime.now(SHANGHAI)


def iso_now() -> str:
    return shanghai_now().isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redact(value: str) -> str:
    lowered = value.lower()
    if any(token in lowered for token in ("password", "secret", "token", "api_key", "postgres://", "postgresql://")):
        return "<redacted>"
    return value


def print_plan(lines: list[str]) -> None:
    for line in lines:
        print(line, flush=True)


def load_dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BackupError("env_file_unreadable") from exc
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def require_existing_file(path: Path, code: str) -> Path:
    resolved = path.expanduser()
    if not resolved.is_file():
        raise BackupError(code)
    return resolved


def docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def run_docker(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BackupError("docker_not_found") from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupError("docker_timeout") from exc


def require_docker() -> None:
    if not docker_available():
        raise BackupError("docker_unavailable")


def compose_prefix(project: str, env_file: Path | None, compose_file: Path) -> list[str]:
    args = ["compose", "-p", project, "-f", str(compose_file)]
    if env_file is not None:
        args.extend(["--env-file", str(env_file)])
    return args


def volume_name(project: str, suffix: str) -> str:
    return f"{project}_{suffix}"


def is_denied_project(project: str) -> bool:
    return project.strip().lower() in PRODUCTION_PROJECTS


def refuse_production_project(project: str) -> None:
    if not project or is_denied_project(project):
        raise BackupError("production_project_refused")


def refuse_worktree_knowledge(path: Path) -> None:
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise BackupError("upload_target_invalid") from exc
    if resolved == WORKTREE_KNOWLEDGE:
        raise BackupError("worktree_knowledge_restore_refused")
    try:
        resolved.relative_to(WORKTREE_KNOWLEDGE)
    except ValueError:
        return
    raise BackupError("worktree_knowledge_restore_refused")


def dir_is_nonempty(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        next(path.iterdir())
    except StopIteration:
        return False
    return True


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def archive_directory(source: Path, archive: Path) -> dict[str, Any]:
    if not source.is_dir():
        raise BackupError("upload_source_missing")
    archive.parent.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            if path.is_symlink():
                continue
            rel = path.relative_to(source).as_posix()
            if rel.startswith("/") or ".." in Path(rel).parts:
                raise BackupError("upload_source_unsafe_path")
            tar.add(path, arcname=rel, recursive=False)
            files.append(
                {
                    "path": rel,
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
    return {
        "archive": archive.name,
        "sha256": sha256_file(archive),
        "bytes": archive.stat().st_size,
        "file_count": len(files),
        "files": files,
    }


def extract_archive(archive: Path, destination: Path) -> None:
    if not archive.is_file():
        raise BackupError("upload_archive_missing")
    refuse_worktree_knowledge(destination)
    if dir_is_nonempty(destination):
        raise BackupError("upload_target_not_empty")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = []
        for member in tar.getmembers():
            name = member.name.replace("\\", "/")
            if member.issym() or member.islnk() or name.startswith("/") or ".." in Path(name).parts:
                raise BackupError("upload_archive_unsafe_path")
            member.name = name
            members.append(member)
        tar.extractall(destination, members=members, filter="data")


def default_plan(args: argparse.Namespace) -> list[str]:
    project = args.project or "<compose-project>"
    compose = str(args.compose_file or COMPOSE_FILE)
    upload = str(args.upload_dir) if args.upload_dir else f"volume {project}_appdata:/app/data/knowledge"
    output = str(args.output) if args.output else "<output-dir>/<timestamp>"
    return [
        "Backup plan (secrets omitted):",
        f"- postgres: docker compose -p {project} -f {compose} exec -T db pg_dump -Fc",
        f"- uploads: {upload} -> {output}/uploads/knowledge.tar.gz",
        f"- milvus volumes: {project}_milvusdata, {project}_etcddata, {project}_miniodata",
        "Services milvus, etcd, and minio are stopped for a consistent volume archive, then started again.",
        "Restore requires --confirm-isolated-target and refuses known production project names.",
        "This is not a SQLite-only backup. Development SQLite steps remain in the README",
        "and do not satisfy this procedure.",
    ]


def backup_postgres(output: Path, *, container: str, dry_run: bool) -> dict[str, Any]:
    dest = output / "postgres" / "database.dump"
    info = {
        "format": "custom",
        "file": "postgres/database.dump",
        "container": container,
    }
    if dry_run:
        info["dry_run"] = True
        return info
    require_docker()
    dest.parent.mkdir(parents=True, exist_ok=True)
    remote = "/tmp/ai-assistant-pg.dump"
    dump = run_docker(
        [
            "exec",
            container,
            "sh",
            "-c",
            f'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f {remote}',
        ],
        timeout=600,
    )
    if dump.returncode != 0:
        run_docker(["exec", container, "rm", "-f", remote], timeout=30)
        raise BackupError("postgres_dump_failed")
    copied = run_docker(["cp", f"{container}:{remote}", str(dest)], timeout=600)
    run_docker(["exec", container, "rm", "-f", remote], timeout=30)
    if copied.returncode != 0 or not dest.is_file():
        raise BackupError("postgres_dump_copy_failed")
    version = run_docker(["exec", container, "pg_dump", "--version"], timeout=30)
    info["sha256"] = sha256_file(dest)
    info["bytes"] = dest.stat().st_size
    if version.returncode == 0:
        info["pg_dump_version"] = version.stdout.strip()
    return info


def restore_postgres(archive: Path, *, container: str, dry_run: bool) -> dict[str, Any]:
    info = {"file": "postgres/database.dump", "container": container}
    if dry_run:
        info["dry_run"] = True
        return info
    if not archive.is_file():
        raise BackupError("postgres_archive_missing")
    require_docker()
    remote = "/tmp/ai-assistant-pg.restore"
    copied = run_docker(["cp", str(archive), f"{container}:{remote}"], timeout=600)
    if copied.returncode != 0:
        raise BackupError("postgres_restore_copy_failed")
    restore = run_docker(
        [
            "exec",
            container,
            "sh",
            "-c",
            f'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB" {remote}',
        ],
        timeout=600,
    )
    run_docker(["exec", container, "rm", "-f", remote], timeout=30)
    if restore.returncode != 0:
        raise BackupError("postgres_restore_failed")
    info["sha256"] = sha256_file(archive)
    info["bytes"] = archive.stat().st_size
    return info


def pick_tar_image() -> str:
    for image in TAR_IMAGE_CANDIDATES:
        inspect = run_docker(["image", "inspect", image], timeout=30)
        if inspect.returncode == 0:
            return image
    raise BackupError("volume_archive_image_missing")


def volume_exists(name: str) -> bool:
    result = run_docker(["volume", "inspect", name], timeout=30)
    return result.returncode == 0


def volume_is_empty(name: str, image: str) -> bool:
    result = run_docker(
        [
            "run",
            "--rm",
            "-v",
            f"{name}:/data:ro",
            image,
            "sh",
            "-c",
            'find /data -mindepth 1 -maxdepth 1 | head -n 1',
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise BackupError("volume_inspect_failed")
    return not result.stdout.strip()


def archive_volume(name: str, dest: Path, image: str) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    result = run_docker(
        [
            "run",
            "--rm",
            "-v",
            f"{name}:/source:ro",
            "-v",
            f"{dest.parent.resolve()}:/dest",
            image,
            "tar",
            "-C",
            "/source",
            "-czf",
            f"/dest/{dest.name}",
            ".",
        ],
        timeout=600,
    )
    if result.returncode != 0 or not dest.is_file():
        raise BackupError("volume_archive_failed")
    return {"volume": name, "file": dest.name, "sha256": sha256_file(dest), "bytes": dest.stat().st_size}


def extract_volume(archive: Path, name: str, image: str) -> None:
    if not archive.is_file():
        raise BackupError("volume_archive_missing")
    if not volume_exists(name):
        created = run_docker(["volume", "create", name], timeout=30)
        if created.returncode != 0:
            raise BackupError("volume_create_failed")
    if not volume_is_empty(name, image):
        raise BackupError("volume_target_not_empty")
    result = run_docker(
        [
            "run",
            "--rm",
            "-v",
            f"{name}:/dest",
            "-v",
            f"{archive.parent.resolve()}:/src:ro",
            image,
            "tar",
            "-C",
            "/dest",
            "-xzf",
            f"/src/{archive.name}",
        ],
        timeout=600,
    )
    if result.returncode != 0:
        raise BackupError("volume_restore_failed")


def compose_service_action(prefix: list[str], action: str, services: list[str], dry_run: bool) -> None:
    if dry_run:
        return
    result = run_docker(prefix + [action, *services], timeout=180)
    if result.returncode != 0:
        raise BackupError("compose_service_control_failed")


def resolve_postgres_container(args: argparse.Namespace) -> str:
    if args.postgres_container:
        return args.postgres_container
    if not args.project:
        raise BackupError("postgres_container_unspecified")
    return f"{args.project}-db-1"


def resolve_upload_source(args: argparse.Namespace) -> Path | None:
    if args.upload_dir:
        return Path(args.upload_dir)
    return None


def backup_uploads_from_volume(output: Path, *, project: str, dry_run: bool) -> dict[str, Any]:
    volume = volume_name(project, "appdata")
    info: dict[str, Any] = {"source": f"{volume}:/app/data/knowledge", "archive": "uploads/knowledge.tar.gz"}
    if dry_run:
        info["dry_run"] = True
        return info
    require_docker()
    if not volume_exists(volume):
        raise BackupError("upload_volume_missing")
    image = pick_tar_image()
    staging = Path(tempfile.mkdtemp(prefix="ai-assistant-uploads-"))
    try:
        copy = run_docker(
            [
                "run",
                "--rm",
                "-v",
                f"{volume}:/source:ro",
                "-v",
                f"{staging.resolve()}:/dest",
                image,
                "sh",
                "-c",
                (
                    "if [ -d /source/knowledge ]; then tar -C /source/knowledge -cf - .; "
                    "else tar -C /source -cf - --files-from /dev/null; fi | tar -C /dest -xf -"
                ),
            ],
            timeout=600,
        )
        if copy.returncode != 0:
            raise BackupError("upload_volume_copy_failed")
        archive = output / "uploads" / "knowledge.tar.gz"
        info.update(archive_directory(staging, archive))
        write_json(output / "uploads" / "files.json", {"files": info.pop("files")})
        return info
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def restore_uploads_to_volume(archive: Path, *, project: str, dry_run: bool) -> dict[str, Any]:
    volume = volume_name(project, "appdata")
    info = {"target": f"{volume}:/app/data/knowledge", "archive": "uploads/knowledge.tar.gz"}
    if dry_run:
        info["dry_run"] = True
        return info
    require_docker()
    image = pick_tar_image()
    if not volume_exists(volume):
        created = run_docker(["volume", "create", volume], timeout=30)
        if created.returncode != 0:
            raise BackupError("upload_volume_create_failed")
    check = run_docker(
        [
            "run",
            "--rm",
            "-v",
            f"{volume}:/dest",
            image,
            "sh",
            "-c",
            'if [ -d /dest/knowledge ]; then find /dest/knowledge -mindepth 1 -maxdepth 1 | head -n 1; fi',
        ],
        timeout=120,
    )
    if check.returncode != 0:
        raise BackupError("upload_volume_inspect_failed")
    if check.stdout.strip():
        raise BackupError("upload_target_not_empty")
    result = run_docker(
        [
            "run",
            "--rm",
            "-v",
            f"{volume}:/dest",
            "-v",
            f"{archive.parent.resolve()}:/src:ro",
            image,
            "sh",
            "-c",
            'mkdir -p /dest/knowledge && tar -C /dest/knowledge -xzf /src/knowledge.tar.gz',
        ],
        timeout=600,
    )
    if result.returncode != 0:
        raise BackupError("upload_volume_restore_failed")
    info["sha256"] = sha256_file(archive)
    info["bytes"] = archive.stat().st_size
    return info


def backup_milvus_volumes(
    output: Path, *, project: str, compose_args: list[str] | None, dry_run: bool
) -> dict[str, Any]:
    names = {suffix: volume_name(project, suffix) for suffix in MILVUS_VOLUMES}
    info: dict[str, Any] = {"mode": "volumes", "volumes": names, "files": {}}
    if dry_run:
        info["dry_run"] = True
        return info
    require_docker()
    for name in names.values():
        if not volume_exists(name):
            raise BackupError("milvus_volume_missing")
    if compose_args:
        compose_service_action(compose_args, "stop", ["milvus", "etcd", "minio"], dry_run=False)
    try:
        image = pick_tar_image()
        dest_dir = output / "milvus"
        dest_dir.mkdir(parents=True, exist_ok=True)
        files = {}
        for suffix, name in names.items():
            files[suffix] = archive_volume(name, dest_dir / f"{suffix}.tar.gz", image)
        info["files"] = files
        return info
    finally:
        if compose_args:
            compose_service_action(compose_args, "start", ["etcd", "minio", "milvus"], dry_run=False)


def restore_milvus_volumes(backup_dir: Path, *, project: str, dry_run: bool) -> dict[str, Any]:
    names = {suffix: volume_name(project, suffix) for suffix in MILVUS_VOLUMES}
    info: dict[str, Any] = {"mode": "volumes", "volumes": names}
    if dry_run:
        info["dry_run"] = True
        return info
    require_docker()
    image = pick_tar_image()
    for suffix, name in names.items():
        extract_volume(backup_dir / "milvus" / f"{suffix}.tar.gz", name, image)
    return info


def build_backup_manifest(
    created_at: str, output: Path, components: dict[str, Any], source: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at,
        "output": output.name,
        "source": source,
        "components": components,
    }


def cmd_backup(args: argparse.Namespace) -> int:
    plan = default_plan(args)
    if args.dry_run and not args.output:
        print_plan(plan)
        print(f"docker_available={docker_available()}")
        if not COMPOSE_FILE.is_file():
            raise BackupError("compose_file_missing")
        print("dry-run ok")
        return 0

    if not args.output:
        raise BackupError("output_required")
    output_root = Path(args.output).expanduser()
    if args.stamp:
        output = output_root / args.stamp
    else:
        output = output_root / shanghai_now().strftime("%Y%m%dT%H%M%S%z")
    if output.exists() and dir_is_nonempty(output):
        raise BackupError("output_not_empty")

    env_file = require_existing_file(Path(args.env_file), "env_file_missing") if args.env_file else None
    compose_file = Path(args.compose_file) if args.compose_file else COMPOSE_FILE
    if not args.skip_postgres or (not args.skip_milvus and args.project and not args.skip_compose_control):
        if not compose_file.is_file() and not args.postgres_container:
            raise BackupError("compose_file_missing")

    components: dict[str, Any] = {}
    source = {
        "project": args.project,
        "compose_file": str(compose_file) if compose_file.is_file() else None,
        "upload_dir": str(args.upload_dir) if args.upload_dir else None,
    }
    compose_args = None
    if args.project and compose_file.is_file() and not args.skip_compose_control:
        compose_args = compose_prefix(args.project, env_file, compose_file)

    if args.dry_run:
        print_plan(plan)
        print(f"output={output}")
        print(f"docker_available={docker_available()}")

    if not args.dry_run:
        output.mkdir(parents=True, exist_ok=True)

    if not args.skip_postgres:
        components["postgres"] = backup_postgres(
            output,
            container=resolve_postgres_container(args),
            dry_run=args.dry_run,
        )
    if not args.skip_uploads:
        upload_dir = resolve_upload_source(args)
        if upload_dir is not None:
            if args.dry_run:
                components["uploads"] = {
                    "source": str(upload_dir),
                    "archive": "uploads/knowledge.tar.gz",
                    "dry_run": True,
                }
            else:
                archive = output / "uploads" / "knowledge.tar.gz"
                detail = archive_directory(Path(upload_dir), archive)
                write_json(output / "uploads" / "files.json", {"files": detail.pop("files")})
                components["uploads"] = detail
                components["uploads"]["source"] = str(Path(upload_dir))
        elif args.project:
            components["uploads"] = backup_uploads_from_volume(output, project=args.project, dry_run=args.dry_run)
        else:
            raise BackupError("upload_source_unspecified")
    if not args.skip_milvus:
        if not args.project:
            raise BackupError("milvus_project_unspecified")
        components["milvus"] = backup_milvus_volumes(
            output,
            project=args.project,
            compose_args=compose_args,
            dry_run=args.dry_run,
        )

    manifest = build_backup_manifest(iso_now(), output, components, source)
    if not args.dry_run:
        write_json(output / "manifest.json", manifest)
        print(f"backup_written={output}")
    else:
        print("dry-run ok")
    print(f"components={','.join(components)}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    if not args.source:
        raise BackupError("restore_source_required")
    backup_dir = Path(args.source).expanduser()
    if not args.dry_run and not (backup_dir / "manifest.json").is_file():
        raise BackupError("restore_manifest_missing")
    if not args.confirm_isolated_target and not args.dry_run:
        raise BackupError("isolated_target_confirmation_required")
    if args.project:
        refuse_production_project(args.project)
    elif not args.upload_dir and not args.postgres_container:
        raise BackupError("restore_target_unspecified")

    components: dict[str, Any] = {}
    if not args.skip_postgres:
        components["postgres"] = restore_postgres(
            backup_dir / "postgres" / "database.dump",
            container=resolve_postgres_container(args),
            dry_run=args.dry_run,
        )
    if not args.skip_uploads:
        archive = backup_dir / "uploads" / "knowledge.tar.gz"
        if args.upload_dir:
            if args.dry_run:
                refuse_worktree_knowledge(Path(args.upload_dir))
                components["uploads"] = {"target": str(args.upload_dir), "dry_run": True}
            else:
                extract_archive(archive, Path(args.upload_dir))
                components["uploads"] = {"target": str(args.upload_dir), "sha256": sha256_file(archive)}
        elif args.project:
            components["uploads"] = restore_uploads_to_volume(archive, project=args.project, dry_run=args.dry_run)
        else:
            raise BackupError("upload_target_unspecified")
    if not args.skip_milvus:
        if not args.project:
            raise BackupError("milvus_project_unspecified")
        components["milvus"] = restore_milvus_volumes(backup_dir, project=args.project, dry_run=args.dry_run)
    print(f"restore_{'dry-run' if args.dry_run else 'completed'}={backup_dir}")
    print(f"components={','.join(components)}")
    return 0


def _write_marker_tree(root: Path, payload: str) -> Path:
    path = root / "tenant-drill" / "marker.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


def _assert(condition: bool, code: str) -> None:
    if not condition:
        raise BackupError(code)


def cmd_self_check(args: argparse.Namespace) -> int:
    failures: list[str] = []

    def expect_error(code: str, func: Any) -> None:
        try:
            func()
            failures.append(f"expected_{code}")
        except BackupError as exc:
            if str(exc) != code:
                failures.append(f"{code}_got_{exc}")

    expect_error("production_project_refused", lambda: refuse_production_project("production"))
    expect_error("production_project_refused", lambda: refuse_production_project("ai-assistant"))
    expect_error("worktree_knowledge_restore_refused", lambda: refuse_worktree_knowledge(WORKTREE_KNOWLEDGE))

    with tempfile.TemporaryDirectory(prefix="ai-assistant-backup-check-") as raw:
        base = Path(raw)
        source = base / "src"
        restored = base / "dst"
        output = base / "backups"
        marker = "nfr-backup-marker\n"
        _write_marker_tree(source, marker)
        archive = output / "uploads" / "knowledge.tar.gz"
        detail = archive_directory(source, archive)
        _assert(detail["file_count"] == 1, "upload_archive_count")
        extract_archive(archive, restored)
        restored_file = restored / "tenant-drill" / "marker.txt"
        _assert(restored_file.read_text(encoding="utf-8") == marker, "upload_restore_mismatch")
        source_hash = sha256_file(source / "tenant-drill" / "marker.txt")
        _assert(source_hash == sha256_file(restored_file), "upload_hash_mismatch")
        expect_error("upload_target_not_empty", lambda: extract_archive(archive, restored))
        expect_error("worktree_knowledge_restore_refused", lambda: extract_archive(archive, WORKTREE_KNOWLEDGE))

        parser = build_parser()
        dry = parser.parse_args(["backup", "--dry-run"])
        _assert(cmd_backup(dry) == 0, "dry_run_failed")

        # Restore safety through the CLI.
        fake = base / "empty-backup"
        fake.mkdir()
        write_json(fake / "manifest.json", {"schema_version": SCHEMA_VERSION, "components": {}})
        expect_error(
            "isolated_target_confirmation_required",
            lambda: cmd_restore(
                parser.parse_args(
                    [
                        "restore",
                        "--from",
                        str(fake),
                        "--project",
                        "nfr010-drill-target",
                        "--skip-postgres",
                        "--skip-milvus",
                        "--upload-dir",
                        str(base / "other"),
                    ]
                )
            ),
        )
        expect_error(
            "production_project_refused",
            lambda: cmd_restore(
                parser.parse_args(
                    [
                        "restore",
                        "--from",
                        str(fake),
                        "--project",
                        "prod",
                        "--confirm-isolated-target",
                        "--skip-postgres",
                        "--skip-uploads",
                        "--skip-milvus",
                    ]
                )
            ),
        )

    if failures:
        print("self-check failed: " + ",".join(failures), file=sys.stderr)
        return 1
    print("self-check ok")
    print("covered=uploads,dry-run,restore-safety")
    print(f"docker_available={docker_available()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(block: argparse.ArgumentParser) -> None:
        block.add_argument("--project", help="Docker Compose project name")
        block.add_argument("--env-file", help="Compose env file; contents are never printed")
        block.add_argument("--compose-file", default=str(COMPOSE_FILE), help="Compose file path")
        block.add_argument("--postgres-container", help="Explicit PostgreSQL container name")
        block.add_argument("--upload-dir", help="Host path to the knowledge upload directory")
        block.add_argument("--skip-postgres", action="store_true")
        block.add_argument("--skip-uploads", action="store_true")
        block.add_argument("--skip-milvus", action="store_true")
        block.add_argument("--skip-compose-control", action="store_true", help="Do not stop or start Compose services")
        block.add_argument("--dry-run", action="store_true")

    backup = sub.add_parser("backup", help="Create a recoverable backup directory")
    add_common(backup)
    backup.add_argument("--output", help="Directory that will receive a timestamped backup folder")
    backup.add_argument("--stamp", help="Override the backup folder name")
    backup.set_defaults(func=cmd_backup)

    restore = sub.add_parser("restore", help="Restore into an isolated empty target")
    add_common(restore)
    restore.add_argument("--from", dest="source", help="Backup directory that contains manifest.json")
    restore.add_argument(
        "--confirm-isolated-target",
        action="store_true",
        help="Required acknowledgement that the target is an isolated empty environment",
    )
    restore.set_defaults(func=cmd_restore)

    check = sub.add_parser("self-check", help="Run isolated fixture checks that do not touch production data")
    check.set_defaults(func=cmd_self_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except BackupError as exc:
        print(f"error={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
