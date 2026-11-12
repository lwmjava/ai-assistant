"""审计日志保留期与归档清理。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlmodel import Session, col, select

from app.audit.models import AuditAction, AuditLog
from app.audit.retention import (
    MIN_RETENTION_DAYS,
    AuditRetentionError,
    archive_and_cleanup_expired_logs,
)
from app.core.config import Settings
from app.core.database import engine
from scripts.archive_audit_logs import main as archive_script_main


def _insert_log(*, action: str, created_at: datetime, details: str | None = None) -> AuditLog:
    record = AuditLog(action=action, details=details, created_at=created_at, updated_at=created_at)
    with Session(engine) as session:
        session.add(record)
        session.commit()
        session.refresh(record)
        session.expunge(record)
        return record


def _get(record_id: str) -> AuditLog | None:
    with Session(engine) as session:
        return session.get(AuditLog, record_id)


def _delete_ids(*record_ids: str | None) -> None:
    with Session(engine) as session:
        for record_id in record_ids:
            if not record_id:
                continue
            row = session.get(AuditLog, record_id)
            if row is not None:
                session.delete(row)
        session.commit()


def test_retention_days_default_is_at_least_90() -> None:
    settings = Settings()
    assert settings.AUDIT_RETENTION_DAYS >= MIN_RETENTION_DAYS
    assert Settings.model_fields["AUDIT_RETENTION_DAYS"].default == 90


def test_retention_days_below_90_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(AUDIT_RETENTION_DAYS=89)


def test_runtime_cleanup_rejects_retention_below_90(tmp_path: Path) -> None:
    with pytest.raises(AuditRetentionError):
        archive_and_cleanup_expired_logs(
            retention_days=89,
            archive_dir=tmp_path,
            dry_run=True,
        )


def test_expired_rows_are_archived_then_removed(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    expired = _insert_log(
        action="user_login",
        created_at=now - timedelta(days=91),
        details=json.dumps({"case": "expired"}),
    )
    recent = _insert_log(
        action="user_logout",
        created_at=now - timedelta(days=1),
        details=json.dumps({"case": "recent"}),
    )
    cleanup_id = None
    try:
        result = archive_and_cleanup_expired_logs(archive_dir=tmp_path, dry_run=False)
        cleanup_id = result.cleanup_record_id
        assert result.expired_count == 1
        assert result.archived_count == 1
        assert result.deleted_count == 1
        assert result.archive_path is not None and result.archive_path.exists()
        archived = [json.loads(line) for line in result.archive_path.read_text(encoding="utf-8").splitlines()]
        assert [row["id"] for row in archived] == [expired.id]
        assert _get(expired.id) is None
        assert _get(recent.id) is not None
        cleanup = _get(result.cleanup_record_id or "")
        assert cleanup is not None
        assert cleanup.action == AuditAction.AUDIT_RETENTION_RUN.value
        raw_details = cleanup.details or "{}"
        payload = raw_details if isinstance(raw_details, dict) else json.loads(raw_details)
        assert payload["deleted_count"] == 1
        assert payload["archived_count"] == 1
        assert payload["retention_days"] >= MIN_RETENTION_DAYS
    finally:
        _delete_ids(expired.id, recent.id, cleanup_id)


def test_dry_run_does_not_archive_or_delete(tmp_path: Path) -> None:
    expired = _insert_log(
        action="user_login",
        created_at=datetime.now(UTC) - timedelta(days=120),
    )
    try:
        result = archive_and_cleanup_expired_logs(archive_dir=tmp_path, dry_run=True)
        assert result.dry_run is True
        assert result.expired_count == 1
        assert result.archived_count == 0
        assert result.deleted_count == 0
        assert result.archive_path is None
        assert result.cleanup_record_id is None
        assert _get(expired.id) is not None
        assert list(tmp_path.glob("*.jsonl")) == []
    finally:
        _delete_ids(expired.id)


def test_archive_failure_leaves_live_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expired = _insert_log(
        action="user_login",
        created_at=datetime.now(UTC) - timedelta(days=100),
    )
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")

    def fail_write(path, rows):  # noqa: ANN001
        raise OSError("archive write failed")

    monkeypatch.setattr("app.audit.retention._atomic_write_jsonl", fail_write)
    try:
        with pytest.raises(OSError):
            archive_and_cleanup_expired_logs(archive_dir=blocker, dry_run=False)
        assert _get(expired.id) is not None
        with Session(engine) as session:
            runs = list(
                session.exec(
                    select(AuditLog).where(col(AuditLog.action) == AuditAction.AUDIT_RETENTION_RUN.value)
                )
            )
            assert runs == []
    finally:
        _delete_ids(expired.id)


def test_script_defaults_to_dry_run(tmp_path: Path) -> None:
    expired = _insert_log(
        action="user_login",
        created_at=datetime.now(UTC) - timedelta(days=100),
    )
    try:
        code = archive_script_main(["--archive-dir", str(tmp_path)])
        assert code == 0
        assert _get(expired.id) is not None
        assert list(tmp_path.glob("*.jsonl")) == []
    finally:
        _delete_ids(expired.id)


def test_script_execute_archives_and_records(tmp_path: Path) -> None:
    expired = _insert_log(
        action="user_login",
        created_at=datetime.now(UTC) - timedelta(days=100),
    )
    cleanup_id = None
    try:
        code = archive_script_main(["--execute", "--archive-dir", str(tmp_path)])
        assert code == 0
        assert _get(expired.id) is None
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        with Session(engine) as session:
            runs = list(
                session.exec(
                    select(AuditLog).where(col(AuditLog.action) == AuditAction.AUDIT_RETENTION_RUN.value)
                )
            )
            assert len(runs) == 1
            cleanup_id = runs[0].id
    finally:
        _delete_ids(expired.id, cleanup_id)
