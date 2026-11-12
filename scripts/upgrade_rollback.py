"""Upgrade and roll back a downtime release on isolated targets.

The operator flow is: stop writes, take a pre-upgrade backup, migrate,
start, then verify. Rollback restores that backup so the service and
data return to the pre-upgrade state. This procedure requires downtime
and is not a blue-green or zero-downtime rollout. It does not change
production defaults, and it does not measure RTO or RPO.

Live actions refuse known production project names and never write to
the worktree knowledge directory. In-place volume replacement is limited
to confirmed isolated targets whose volume names match that project.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_BACKUP_PATH = Path(__file__).resolve().parent / "backup_restore.py"

SCHEMA_VERSION = "upgrade-1.0.0"
PRE_VERSION = "0.1.0-drill"
POST_VERSION = "0.2.0-drill"
PRE_REVISION = "rev_drill_pre"
POST_REVISION = "rev_drill_post"
PRE_MARKER = "upgrade-pre-marker"
POST_MARKER = "upgrade-post-marker"
APP_SERVICE = "app"
WRITE_SERVICES = ("app",)
DRILL_PROJECT_PREFIX = "upgd"
PROTECTED_VOLUME_PREFIXES = (
    "ai-assistant_",
    "nfr003-",
    "rag015-",
    "tmp_rag015-",
    "mnc_",
    "vdb-acceptance",
    "mp-etcd",
)


def _load_backup_restore() -> Any:
    spec = importlib.util.spec_from_file_location("backup_restore", _BACKUP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("backup_restore_unreadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


br = _load_backup_restore()


class UpgradeError(RuntimeError):
    """Stable, user-safe failure code."""


def _raise(code: str) -> None:
    raise UpgradeError(code)


def shanghai_now() -> Any:
    return br.shanghai_now()


def iso_now() -> str:
    return br.iso_now()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    br.write_json(path, payload)


def print_plan(lines: list[str]) -> None:
    br.print_plan(lines)


def default_upgrade_plan(args: argparse.Namespace) -> list[str]:
    project = args.project or "<compose-project>"
    compose = str(getattr(args, "compose_file", None) or br.COMPOSE_FILE)
    migrate = getattr(args, "migrate_command", None) or "python -m app.cli migrate --yes"
    backup_out = getattr(args, "output", None) or "<output-dir>/<timestamp>"
    return [
        "Upgrade plan (secrets omitted):",
        f"1. Stop writes: docker compose -p {project} -f {compose} stop {APP_SERVICE}",
        f"2. Take a complete pre-upgrade backup into {backup_out}",
        f"3. Migrate: {migrate}",
        f"4. Start: docker compose -p {project} -f {compose} start {APP_SERVICE}",
        "5. Verify health, schema revision, and representative data.",
        "Rollback plan (secrets omitted):",
        "1. Stop writes on the upgraded application.",
        "2. Restore the pre-upgrade backup (PostgreSQL, uploads, Milvus volumes).",
        "3. Start the previous application version.",
        "4. Verify that schema and data match the pre-upgrade backup.",
        "This is a downtime upgrade. It is not a zero-downtime or blue-green rollout.",
        "Known production project names are refused.",
        "The worktree knowledge directory is never a restore target.",
        "In-place volume replacement is allowed only on a confirmed isolated target.",
        "This procedure does not measure or certify RTO/RPO.",
    ]


def refuse_upgrade_project(project: str) -> None:
    if not project:
        _raise("project_required")
    br.refuse_production_project(project)


def is_protected_volume(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.startswith(prefix.lower()) for prefix in PROTECTED_VOLUME_PREFIXES)


def assert_project_volume(project: str, name: str) -> None:
    refuse_upgrade_project(project)
    if is_protected_volume(name):
        _raise("protected_volume_refused")
    allowed = {br.volume_name(project, suffix) for suffix in br.MILVUS_VOLUMES}
    if name not in allowed:
        _raise("volume_not_owned_by_project")


def volume_in_use(name: str) -> bool:
    result = br.run_docker(
        ["ps", "-a", "--filter", f"volume={name}", "--format", "{{.ID}}"],
        timeout=30,
    )
    if result.returncode != 0:
        raise UpgradeError("volume_inspect_failed")
    return bool(result.stdout.strip())


def reset_isolated_volume(name: str, project: str) -> None:
    assert_project_volume(project, name)
    if volume_in_use(name):
        _raise("volume_in_use")
    if br.volume_exists(name):
        removed = br.run_docker(["volume", "rm", name], timeout=60)
        if removed.returncode != 0:
            _raise("volume_reset_failed")
    created = br.run_docker(["volume", "create", name], timeout=30)
    if created.returncode != 0:
        _raise("volume_create_failed")


def replace_isolated_upload_dir(archive: Path, destination: Path) -> None:
    br.refuse_worktree_knowledge(destination)
    if destination.exists() and not destination.is_dir():
        _raise("upload_target_invalid")
    if destination.exists():
        for child in destination.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        destination.mkdir(parents=True, exist_ok=True)
    br.extract_archive(archive, destination)


def require_confirm(args: argparse.Namespace) -> None:
    if not getattr(args, "confirm_isolated_target", False):
        _raise("isolated_target_confirmation_required")


def load_manifest(backup_dir: Path) -> dict[str, Any]:
    path = backup_dir / "manifest.json"
    if not path.is_file():
        _raise("restore_manifest_missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpgradeError("restore_manifest_invalid") from exc
    if not isinstance(payload, dict):
        _raise("restore_manifest_invalid")
    return payload


LAST_PSQL_ERROR = ""


def _sanitize_psql_error(text: str) -> str:
    cleaned = br.redact(text or "")
    return " ".join(cleaned.split())[:400]


def psql(container: str, sql: str, *, timeout: int = 60) -> str:
    global LAST_PSQL_ERROR
    LAST_PSQL_ERROR = ""
    remote = "/tmp/ai-assistant-upgrade.sql"
    local_dir = Path(tempfile.mkdtemp(prefix="ai-assistant-psql-"))
    local = local_dir / "stmt.sql"
    try:
        local.write_text(sql.strip() + "\n", encoding="utf-8")
        copied = br.run_docker(["cp", str(local), f"{container}:{remote}"], timeout=30)
        if copied.returncode != 0:
            LAST_PSQL_ERROR = _sanitize_psql_error(copied.stderr)
            raise UpgradeError("postgres_query_failed")
        result = br.run_docker(
            [
                "exec",
                container,
                "psql",
                "-U",
                "drill",
                "-d",
                "assistant",
                "-v",
                "ON_ERROR_STOP=1",
                "-tA",
                "-f",
                remote,
            ],
            timeout=timeout,
        )
        br.run_docker(["exec", container, "rm", "-f", remote], timeout=15)
        if result.returncode != 0:
            LAST_PSQL_ERROR = _sanitize_psql_error(result.stderr or result.stdout)
            raise UpgradeError("postgres_query_failed")
        return result.stdout.strip()
    finally:
        shutil.rmtree(local_dir, ignore_errors=True)


def wait_for_postgres(container: str, *, attempts: int = 40) -> None:
    for _ in range(attempts):
        result = br.run_docker(
            ["exec", container, "pg_isready", "-U", "drill", "-d", "assistant"],
            timeout=15,
        )
        if result.returncode == 0:
            return
        time.sleep(0.5)
    raise UpgradeError("postgres_not_ready")


def init_pre_schema(container: str) -> None:
    psql(
        container,
        """
        DROP TABLE IF EXISTS upgrade_probe;
        DROP TABLE IF EXISTS alembic_version;
        CREATE TABLE alembic_version (
            version_num VARCHAR(32) NOT NULL PRIMARY KEY
        );
        INSERT INTO alembic_version (version_num) VALUES ('rev_drill_pre');
        CREATE TABLE upgrade_probe (
            id INTEGER PRIMARY KEY,
            marker TEXT NOT NULL,
            app_version TEXT NOT NULL
        );
        INSERT INTO upgrade_probe (id, marker, app_version)
        VALUES (1, 'upgrade-pre-marker', '0.1.0-drill');
        """,
    )


def migrate_post_schema(container: str) -> None:
    psql(
        container,
        """
        ALTER TABLE upgrade_probe ADD COLUMN extra_flag TEXT;
        UPDATE upgrade_probe
           SET extra_flag = 'post-upgrade',
               app_version = '0.2.0-drill',
               marker = 'upgrade-post-marker'
         WHERE id = 1;
        ALTER TABLE upgrade_probe ALTER COLUMN extra_flag SET NOT NULL;
        DELETE FROM alembic_version;
        INSERT INTO alembic_version (version_num) VALUES ('rev_drill_post');
        """,
    )


def read_schema_state(container: str) -> dict[str, str]:
    revision = psql(container, "SELECT version_num FROM alembic_version;")
    columns = psql(
        container,
        "SELECT string_agg(column_name, ',' ORDER BY ordinal_position) "
        "FROM information_schema.columns "
        "WHERE table_name = 'upgrade_probe';",
    )
    column_names = [item.strip() for item in columns.split(",") if item.strip()]
    if "extra_flag" in column_names:
        row = psql(
            container,
            "SELECT marker || '|' || app_version || '|' || coalesce(extra_flag, '') "
            "FROM upgrade_probe WHERE id = 1;",
        )
    else:
        row = psql(
            container,
            "SELECT marker || '|' || app_version FROM upgrade_probe WHERE id = 1;",
        )
    parts = row.split("|")
    marker = parts[0] if parts else ""
    version = parts[1] if len(parts) > 1 else ""
    extra = parts[2] if len(parts) > 2 else ""
    return {
        "revision": revision,
        "columns": ",".join(column_names),
        "marker": marker,
        "app_version": version,
        "extra_flag": extra,
    }


def write_probe_volume(name: str, payload: str, image: str) -> None:
    result = br.run_docker(
        [
            "run",
            "--rm",
            "-v",
            f"{name}:/data",
            image,
            "sh",
            "-c",
            f"printf '%s\\n' '{payload}' > /data/probe.txt",
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise UpgradeError("volume_probe_write_failed")


def read_probe_volume(name: str, image: str) -> str:
    result = br.run_docker(
        [
            "run",
            "--rm",
            "-v",
            f"{name}:/data:ro",
            image,
            "sh",
            "-c",
            "if [ -f /data/probe.txt ]; then cat /data/probe.txt; fi",
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise UpgradeError("volume_probe_read_failed")
    return result.stdout.strip()


def start_drill_postgres(container: str, password: str) -> None:
    result = br.run_docker(
        [
            "run",
            "-d",
            "--name",
            container,
            "--pull=never",
            "--tmpfs",
            "/var/lib/postgresql/data",
            "-e",
            "POSTGRES_USER=drill",
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-e",
            "POSTGRES_DB=assistant",
            "postgres:16-alpine",
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise UpgradeError("postgres_start_failed")


def remove_container(container: str) -> None:
    br.run_docker(["rm", "-f", container], timeout=60)


def remove_volume_if_owned(name: str, project: str) -> None:
    assert_project_volume(project, name)
    if volume_in_use(name):
        _raise("volume_in_use")
    if br.volume_exists(name):
        removed = br.run_docker(["volume", "rm", name], timeout=60)
        if removed.returncode != 0:
            _raise("volume_reset_failed")


def ensure_volumes(project: str, created: dict[str, str]) -> dict[str, str]:
    for suffix in br.MILVUS_VOLUMES:
        name = br.volume_name(project, suffix)
        assert_project_volume(project, name)
        if br.volume_exists(name):
            _raise("volume_already_exists")
        created_vol = br.run_docker(["volume", "create", name], timeout=30)
        if created_vol.returncode != 0:
            _raise("volume_create_failed")
        created[suffix] = name
    return created


def stop_writes(args: argparse.Namespace, *, dry_run: bool) -> None:
    if dry_run:
        return
    if getattr(args, "skip_compose_control", False) or not args.project:
        return
    compose_file = Path(args.compose_file) if args.compose_file else br.COMPOSE_FILE
    if not compose_file.is_file():
        return
    env_file = Path(args.env_file) if args.env_file else None
    prefix = br.compose_prefix(args.project, env_file, compose_file)
    br.compose_service_action(prefix, "stop", list(WRITE_SERVICES), dry_run=False)


def start_app(args: argparse.Namespace, *, dry_run: bool) -> None:
    if dry_run or getattr(args, "skip_compose_control", False) or not args.project:
        return
    compose_file = Path(args.compose_file) if args.compose_file else br.COMPOSE_FILE
    if not compose_file.is_file():
        return
    env_file = Path(args.env_file) if args.env_file else None
    prefix = br.compose_prefix(args.project, env_file, compose_file)
    br.compose_service_action(prefix, "start", list(WRITE_SERVICES), dry_run=False)


def run_migrate(args: argparse.Namespace, *, dry_run: bool) -> None:
    command = getattr(args, "migrate_command", None)
    if not command or dry_run:
        return
    result = __import__("subprocess").run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        raise UpgradeError("migrate_failed")


def backup_args_from_upgrade(args: argparse.Namespace, output: str, stamp: str | None = None) -> argparse.Namespace:
    parser = br.build_parser()
    values = [
        "backup",
        "--output",
        output,
        "--skip-compose-control",
    ]
    if args.project:
        values.extend(["--project", args.project])
    if getattr(args, "env_file", None):
        values.extend(["--env-file", args.env_file])
    if getattr(args, "compose_file", None):
        values.extend(["--compose-file", args.compose_file])
    if getattr(args, "postgres_container", None):
        values.extend(["--postgres-container", args.postgres_container])
    if getattr(args, "upload_dir", None):
        values.extend(["--upload-dir", args.upload_dir])
    if getattr(args, "skip_postgres", False):
        values.append("--skip-postgres")
    if getattr(args, "skip_uploads", False):
        values.append("--skip-uploads")
    if getattr(args, "skip_milvus", False):
        values.append("--skip-milvus")
    if stamp:
        values.extend(["--stamp", stamp])
    if getattr(args, "dry_run", False):
        values.append("--dry-run")
    return parser.parse_args(values)


def restore_in_place(args: argparse.Namespace, backup_dir: Path, *, dry_run: bool) -> dict[str, Any]:
    components: dict[str, Any] = {}
    if not getattr(args, "skip_postgres", False):
        components["postgres"] = br.restore_postgres(
            backup_dir / "postgres" / "database.dump",
            container=br.resolve_postgres_container(args),
            dry_run=dry_run,
        )
    if not getattr(args, "skip_uploads", False):
        archive = backup_dir / "uploads" / "knowledge.tar.gz"
        if not args.upload_dir:
            _raise("upload_target_unspecified")
        target = Path(args.upload_dir)
        br.refuse_worktree_knowledge(target)
        if dry_run:
            components["uploads"] = {"target": str(target), "dry_run": True}
        else:
            replace_isolated_upload_dir(archive, target)
            components["uploads"] = {"target": str(target), "sha256": br.sha256_file(archive)}
    if not getattr(args, "skip_milvus", False):
        if not args.project:
            _raise("milvus_project_unspecified")
        names = {suffix: br.volume_name(args.project, suffix) for suffix in br.MILVUS_VOLUMES}
        if dry_run:
            components["milvus"] = {"volumes": names, "dry_run": True}
        else:
            image = br.pick_tar_image()
            for suffix, name in names.items():
                reset_isolated_volume(name, args.project)
                br.extract_volume(backup_dir / "milvus" / f"{suffix}.tar.gz", name, image)
            components["milvus"] = {"volumes": names, "replaced": True}
    return components


def cmd_check(_args: argparse.Namespace) -> int:
    failures: list[str] = []

    def expect_error(code: str, func: Any) -> None:
        try:
            func()
            failures.append(f"expected_{code}")
        except (UpgradeError, br.BackupError) as exc:
            if str(exc) != code:
                failures.append(f"{code}_got_{exc}")

    expect_error("production_project_refused", lambda: refuse_upgrade_project("production"))
    expect_error("production_project_refused", lambda: refuse_upgrade_project("ai-assistant"))
    expect_error("worktree_knowledge_restore_refused", lambda: br.refuse_worktree_knowledge(br.WORKTREE_KNOWLEDGE))
    expect_error(
        "protected_volume_refused",
        lambda: assert_project_volume(
            "nfr003-accept-20261017",
            "nfr003-accept-20261017_milvusdata",
        ),
    )
    expect_error(
        "volume_not_owned_by_project",
        lambda: assert_project_volume("upgdexample", "other_milvusdata"),
    )

    parser = build_parser()
    dry = parser.parse_args(["upgrade", "--dry-run"])
    expect_error("project_required", lambda: cmd_upgrade(parser.parse_args(["upgrade"])))
    expect_error(
        "isolated_target_confirmation_required",
        lambda: cmd_upgrade(
            parser.parse_args(
                ["upgrade", "--project", "upgdstageonly", "--output", str(Path(tempfile.gettempdir()) / "unused")]
            )
        ),
    )
    expect_error(
        "production_project_refused",
        lambda: cmd_upgrade(
            parser.parse_args(
                [
                    "upgrade",
                    "--project",
                    "prod",
                    "--output",
                    str(Path(tempfile.gettempdir()) / "unused"),
                    "--confirm-isolated-target",
                ]
            )
        ),
    )
    expect_error(
        "isolated_target_confirmation_required",
        lambda: cmd_rollback(
            parser.parse_args(
                [
                    "rollback",
                    "--from",
                    str(Path(tempfile.gettempdir()) / "unused"),
                    "--project",
                    "upgdstageonly",
                    "--upload-dir",
                    str(Path(tempfile.gettempdir()) / "uploads"),
                    "--skip-postgres",
                    "--skip-milvus",
                ]
            )
        ),
    )
    expect_error(
        "production_project_refused",
        lambda: cmd_rollback(
            parser.parse_args(
                [
                    "rollback",
                    "--from",
                    str(Path(tempfile.gettempdir()) / "unused"),
                    "--project",
                    "ai-assistant",
                    "--confirm-isolated-target",
                    "--skip-postgres",
                    "--skip-uploads",
                    "--skip-milvus",
                ]
            )
        ),
    )

    with tempfile.TemporaryDirectory(prefix="ai-assistant-upgrade-check-") as raw:
        base = Path(raw)
        source = base / "src"
        restored = base / "dst"
        source.mkdir()
        marker = source / "tenant-drill" / "marker.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("upgrade-check-marker\n", encoding="utf-8")
        archive = base / "knowledge.tar.gz"
        br.archive_directory(source, archive)
        replace_isolated_upload_dir(archive, restored)
        got = (restored / "tenant-drill" / "marker.txt").read_text(encoding="utf-8")
        if got != "upgrade-check-marker\n":
            failures.append("upload_replace_mismatch")
        expect_error(
            "worktree_knowledge_restore_refused",
            lambda: replace_isolated_upload_dir(archive, br.WORKTREE_KNOWLEDGE),
        )
        rc = cmd_upgrade(dry)
        if rc != 0:
            failures.append("dry_run_failed")

    if failures:
        print("check failed: " + ",".join(failures), file=sys.stderr)
        return 1
    print("check ok")
    print("covered=upgrade-plan,rollback-safety,upload-replace,dry-run")
    print(f"docker_available={br.docker_available()}")
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    plan = default_upgrade_plan(args)
    if args.dry_run and not args.output:
        print_plan(plan)
        print(f"docker_available={br.docker_available()}")
        print("dry-run ok")
        return 0

    refuse_upgrade_project(args.project or "")
    if not args.dry_run:
        require_confirm(args)
    if not args.output:
        _raise("output_required")

    print_plan(plan)
    stop_writes(args, dry_run=args.dry_run)
    backup = backup_args_from_upgrade(args, args.output, stamp=getattr(args, "stamp", None))
    rc = br.cmd_backup(backup)
    if rc != 0:
        raise UpgradeError("pre_upgrade_backup_failed")
    if args.dry_run:
        print("dry-run ok")
        return 0
    run_migrate(args, dry_run=False)
    start_app(args, dry_run=False)
    journal = {
        "schema_version": SCHEMA_VERSION,
        "created_at": iso_now(),
        "project": args.project,
        "from_version": getattr(args, "from_version", None),
        "to_version": getattr(args, "to_version", None),
        "pre_upgrade_backup_root": args.output,
        "steps": ["stop_writes", "pre_upgrade_backup", "migrate", "start"],
    }
    journal_dir = Path(args.output).expanduser()
    write_json(journal_dir / "upgrade-journal.json", journal)
    print(f"upgrade_journal={journal_dir / 'upgrade-journal.json'}")
    print("upgrade completed; verify health, schema revision, and representative data")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    if not args.source:
        _raise("restore_source_required")
    backup_dir = Path(args.source).expanduser()
    if args.project:
        refuse_upgrade_project(args.project)
    if args.dry_run:
        print_plan(default_upgrade_plan(args))
        print("dry-run ok")
        return 0
    require_confirm(args)
    load_manifest(backup_dir)
    if args.upload_dir:
        br.refuse_worktree_knowledge(Path(args.upload_dir))
    stop_writes(args, dry_run=False)
    components = restore_in_place(args, backup_dir, dry_run=False)
    start_app(args, dry_run=False)
    print(f"rollback_completed={backup_dir}")
    print(f"components={','.join(components)}")
    print("verify that schema and data match the pre-upgrade backup")
    return 0


def _write_uploads(root: Path, name: str, payload: str) -> None:
    path = root / "tenant-drill" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def cmd_drill(args: argparse.Namespace) -> int:
    if not args.dry_run:
        require_confirm(args)
        br.require_docker()
        if not br.docker_available():
            _raise("docker_unavailable")

    token = shanghai_now().strftime("%H%M%S") + format(time.time_ns() % 0xFFFFFF, "06x")
    project = f"{DRILL_PROJECT_PREFIX}{token}"
    container = f"upgd-pg-{token}"
    if is_protected_volume(br.volume_name(project, "milvusdata")):
        _raise("protected_volume_refused")

    print_plan(default_upgrade_plan(args))
    if args.dry_run:
        print(f"project={project}")
        print("dry-run ok")
        return 0

    default_root = ROOT / "data" / "validation" / f"upgrade-drill-{token}"
    work_root = Path(args.output).expanduser() if args.output else default_root
    work_root.mkdir(parents=True, exist_ok=True)
    upload_dir = work_root / "uploads-live"
    backup_root = work_root / "backups"
    password = format(time.time_ns(), "x") + token
    image = None
    volumes: dict[str, str] = {}
    created_container = False
    status = "failed"
    notes: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": iso_now(),
        "timezone": "Asia/Shanghai",
        "project": project,
        "postgres_container": container,
        "from_version": PRE_VERSION,
        "to_version": POST_VERSION,
        "breaking": (
            "The post-upgrade probe table requires extra_flag; "
            "rolling back needs the pre-upgrade backup."
        ),
    }

    try:
        image = br.pick_tar_image()
        ensure_volumes(project, volumes)
        for name in volumes.values():
            write_probe_volume(name, "volume-pre", image)
        upload_dir.mkdir(parents=True, exist_ok=True)
        _write_uploads(upload_dir, "marker.txt", PRE_MARKER + "\n")
        start_drill_postgres(container, password)
        created_container = True
        wait_for_postgres(container)
        last_error = None
        for _ in range(10):
            try:
                init_pre_schema(container)
                last_error = None
                break
            except UpgradeError as exc:
                last_error = exc
                time.sleep(0.5)
        if last_error is not None:
            raise last_error
        pre_state = read_schema_state(container)
        if pre_state["revision"] != PRE_REVISION or pre_state["app_version"] != PRE_VERSION:
            _raise("pre_schema_invalid")

        notes["steps"] = ["stop_writes"]
        # No application process writes during the drill after this point.
        backup_ns = backup_args_from_upgrade(
            argparse.Namespace(
                project=project,
                env_file=None,
                compose_file=str(br.COMPOSE_FILE),
                postgres_container=container,
                upload_dir=str(upload_dir),
                skip_postgres=False,
                skip_uploads=False,
                skip_milvus=False,
                dry_run=False,
            ),
            str(backup_root),
            stamp="pre-upgrade",
        )
        rc = br.cmd_backup(backup_ns)
        if rc != 0:
            _raise("pre_upgrade_backup_failed")
        backup_dir = backup_root / "pre-upgrade"
        notes["pre_upgrade_backup"] = str(backup_dir)
        notes["steps"].append("pre_upgrade_backup")

        migrate_post_schema(container)
        notes["steps"].append("migrate")
        _write_uploads(upload_dir, "post.txt", POST_MARKER + "\n")
        for name in volumes.values():
            write_probe_volume(name, "volume-post", image)
        post_state = read_schema_state(container)
        if (
            post_state["revision"] != POST_REVISION
            or post_state["app_version"] != POST_VERSION
            or "extra_flag" not in post_state["columns"]
            or post_state["extra_flag"] != "post-upgrade"
        ):
            _raise("post_schema_invalid")
        if not (upload_dir / "tenant-drill" / "post.txt").is_file():
            _raise("post_upload_missing")
        if any(read_probe_volume(name, image) != "volume-post" for name in volumes.values()):
            _raise("post_volume_invalid")
        notes["steps"].append("start_and_verify_upgrade")
        notes["post_upgrade"] = post_state

        rollback_ns = argparse.Namespace(
            project=project,
            env_file=None,
            compose_file=str(br.COMPOSE_FILE),
            postgres_container=container,
            upload_dir=str(upload_dir),
            skip_postgres=False,
            skip_uploads=False,
            skip_milvus=False,
            skip_compose_control=True,
            dry_run=False,
            confirm_isolated_target=True,
            source=str(backup_dir),
        )
        cmd_rollback(rollback_ns)
        notes["steps"].append("rollback")
        rolled = read_schema_state(container)
        if (
            rolled["revision"] != PRE_REVISION
            or rolled["app_version"] != PRE_VERSION
            or rolled["marker"] != PRE_MARKER
            or "extra_flag" in rolled["columns"]
        ):
            _raise("rollback_schema_mismatch")
        if (upload_dir / "tenant-drill" / "post.txt").exists():
            _raise("rollback_upload_not_reverted")
        pre_upload = (upload_dir / "tenant-drill" / "marker.txt").read_text(encoding="utf-8")
        if pre_upload != PRE_MARKER + "\n":
            _raise("rollback_upload_mismatch")
        if any(read_probe_volume(name, image) != "volume-pre" for name in volumes.values()):
            _raise("rollback_volume_mismatch")
        notes["steps"].append("verify_rollback")
        notes["rolled_back"] = rolled
        status = "passed"
        print("drill passed")
        print(f"from_version={PRE_VERSION}")
        print(f"to_version={POST_VERSION}")
        print(f"rollback_revision={rolled['revision']}")
        return 0
    finally:
        notes["status"] = status
        notes["finished_at"] = iso_now()
        if status != "passed" and LAST_PSQL_ERROR:
            notes["error_detail"] = LAST_PSQL_ERROR
        try:
            write_json(work_root / "drill-result.json", notes)
            print(f"drill_result={work_root / 'drill-result.json'}")
        except OSError:
            pass
        if created_container:
            remove_container(container)
        if volumes:
            for name in volumes.values():
                try:
                    if not volume_in_use(name) and br.volume_exists(name):
                        assert_project_volume(project, name)
                        br.run_docker(["volume", "rm", name], timeout=60)
                except (UpgradeError, br.BackupError):
                    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(block: argparse.ArgumentParser) -> None:
        block.add_argument("--project", help="Docker Compose project name")
        block.add_argument("--env-file", help="Compose env file; contents are never printed")
        block.add_argument("--compose-file", default=str(br.COMPOSE_FILE), help="Compose file path")
        block.add_argument("--postgres-container", help="Explicit PostgreSQL container name")
        block.add_argument("--upload-dir", help="Host path to the knowledge upload directory")
        block.add_argument("--skip-postgres", action="store_true")
        block.add_argument("--skip-uploads", action="store_true")
        block.add_argument("--skip-milvus", action="store_true")
        block.add_argument("--skip-compose-control", action="store_true")
        block.add_argument("--dry-run", action="store_true")
        block.add_argument(
            "--confirm-isolated-target",
            action="store_true",
            help="Required acknowledgement that the target is an isolated environment",
        )

    upgrade = sub.add_parser("upgrade", help="Stop writes, back up, migrate, and start")
    add_common(upgrade)
    upgrade.add_argument("--output", help="Directory that will receive the pre-upgrade backup")
    upgrade.add_argument("--stamp", help="Override the backup folder name")
    upgrade.add_argument(
        "--migrate-command",
        help="Command used to migrate after writes are stopped; not executed during dry-run",
    )
    upgrade.add_argument("--from-version", help="Pre-upgrade application version label")
    upgrade.add_argument("--to-version", help="Target application version label")
    upgrade.set_defaults(func=cmd_upgrade)

    rollback = sub.add_parser("rollback", help="Restore the pre-upgrade backup on an isolated target")
    add_common(rollback)
    rollback.add_argument("--from", dest="source", help="Pre-upgrade backup directory")
    rollback.set_defaults(func=cmd_rollback)

    check = sub.add_parser("check", help="Run isolated fixture checks that do not touch production data")
    check.set_defaults(func=cmd_check)

    drill = sub.add_parser("drill", help="Run a throwaway cross-version upgrade and rollback")
    add_common(drill)
    drill.add_argument("--output", help="Directory for drill artifacts; defaults under data/validation")
    drill.set_defaults(func=cmd_drill)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (UpgradeError, br.BackupError) as exc:
        print(f"error={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
