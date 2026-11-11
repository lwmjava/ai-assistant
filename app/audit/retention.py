"""审计日志保留：超期记录先归档，再从在线表移除。

保留期默认不少于 90 天，可配置更长。归档写到本地目录，不直接丢弃记录。
清理过程本身也会写入一条审计记录。默认不会自动对生产库执行清理。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from app.audit.models import AuditAction, AuditLog
from app.core.database import engine

logger = logging.getLogger(__name__)

MIN_RETENTION_DAYS = 90
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AuditRetentionResult:
    """一次保留清理的结果摘要，不含记录正文。"""

    dry_run: bool
    retention_days: int
    cutoff: datetime
    expired_count: int
    archived_count: int
    deleted_count: int
    archive_path: Path | None
    cleanup_record_id: str | None


class AuditRetentionError(ValueError):
    """保留期或归档条件不满足，拒绝清理。"""


def resolve_archive_dir(raw: str | Path | None = None) -> Path:
    """解析归档目录：相对路径以项目根为准。"""
    if raw is None:
        from app.core.config import get_settings

        raw = get_settings().AUDIT_ARCHIVE_DIR
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


def _serialize_record(record: AuditLog) -> dict[str, Any]:
    details = record.details
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            pass
    return {
        "id": record.id,
        "action": record.action,
        "user_id": record.user_id,
        "tenant_id": record.tenant_id,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "details": details,
        "ip_address": record.ip_address,
        "user_agent": record.user_agent,
        "created_at": _isoformat(record.created_at),
        "updated_at": _isoformat(record.updated_at),
    }


def _atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(handle.name)
    try:
        with handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def archive_and_cleanup_expired_logs(
    *,
    retention_days: int | None = None,
    archive_dir: str | Path | None = None,
    dry_run: bool = False,
    session: Session | None = None,
) -> AuditRetentionResult:
    """归档并移除超过保留期的在线审计记录。

    Args:
        retention_days: 保留天数，缺省读取配置；不得小于 90。
        archive_dir: 归档目录，缺省读取配置。
        dry_run: 为真时只统计，不写归档、不删除、不落清理记录。
        session: 可选外部会话；缺省使用应用引擎新建会话。
    """
    from app.core.config import get_settings

    settings = get_settings()
    days = settings.AUDIT_RETENTION_DAYS if retention_days is None else int(retention_days)
    if days < MIN_RETENTION_DAYS:
        raise AuditRetentionError(f"审计日志保留期不能少于 {MIN_RETENTION_DAYS} 天")

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    cutoff_sql = _as_naive_utc(cutoff)
    archive_root = resolve_archive_dir(archive_dir)

    def _run(active: Session) -> AuditRetentionResult:
        expired = list(
            active.exec(select(AuditLog).where(col(AuditLog.created_at) < cutoff_sql))
        )
        payload = [_serialize_record(item) for item in expired]
        archive_path: Path | None = None
        deleted = 0
        cleanup_id: str | None = None

        if dry_run:
            return AuditRetentionResult(
                dry_run=True,
                retention_days=days,
                cutoff=cutoff,
                expired_count=len(expired),
                archived_count=0,
                deleted_count=0,
                archive_path=None,
                cleanup_record_id=None,
            )

        if payload:
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            archive_path = archive_root / f"audit-logs-{stamp}.jsonl"
            # 同秒重复执行时避免覆盖已写出的归档。
            if archive_path.exists():
                archive_path = archive_root / f"audit-logs-{stamp}-{os.getpid()}.jsonl"
            try:
                _atomic_write_jsonl(archive_path, payload)
            except Exception:
                logger.exception("审计日志归档失败，已中止清理")
                raise

        expired_ids = [item.id for item in expired]
        if expired_ids:
            for item in expired:
                active.delete(item)
            deleted = len(expired_ids)

        record = AuditLog(
            action=AuditAction.AUDIT_RETENTION_RUN.value,
            resource_type="audit_log",
            details=json.dumps(
                {
                    "retention_days": days,
                    "cutoff": cutoff.isoformat(),
                    "expired_count": len(expired_ids),
                    "archived_count": len(payload),
                    "deleted_count": deleted,
                    "archive_file": archive_path.as_posix() if archive_path else None,
                    "dry_run": False,
                },
                ensure_ascii=False,
            ),
        )
        active.add(record)
        active.commit()
        active.refresh(record)
        cleanup_id = record.id
        logger.info(
            "审计日志保留清理完成 expired=%s archived=%s deleted=%s retention_days=%s",
            len(expired_ids),
            len(payload),
            deleted,
            days,
        )
        return AuditRetentionResult(
            dry_run=False,
            retention_days=days,
            cutoff=cutoff,
            expired_count=len(expired_ids),
            archived_count=len(payload),
            deleted_count=deleted,
            archive_path=archive_path,
            cleanup_record_id=cleanup_id,
        )

    if session is not None:
        return _run(session)
    with Session(engine) as owned:
        return _run(owned)
