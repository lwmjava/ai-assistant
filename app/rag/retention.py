"""软删文档的保留期清理。"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, select

from app.audit.logger import get_audit_logger
from app.audit.models import AuditAction
from app.core.config import settings
from app.models.rag import Document, VectorCleanupJob
from app.rag.document_storage import delete_source_file
from app.rag.vectorstore.factory import get_vector_store

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90


def _target() -> tuple[str, str]:
    backend = settings.RAG_VECTOR_STORE.strip().lower()
    # 不保存连接串或 Token；配置漂移时停止消费旧目标任务。
    identity = [settings.MILVUS_URI, settings.MILVUS_COLLECTION] if backend == "milvus" else [settings.DATABASE_URL]
    return backend, hashlib.sha256(repr(identity).encode()).hexdigest()


async def purge_expired_documents(session: Session, *, now: datetime) -> int:
    """单 worker 扫描；失败保留文档/任务，不丢恢复目标。"""
    now = _as_utc(now)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    backend, target = _target()
    rows = session.exec(select(Document).where(col(Document.deleted_at).is_not(None))).all()
    for doc in rows:
        if _as_utc(doc.deleted_at) >= cutoff:
            continue
        existing = session.exec(select(VectorCleanupJob).where(col(VectorCleanupJob.document_id) == doc.id)).first()
        if existing is None:
            session.add(
                VectorCleanupJob(
                    tenant_id=doc.tenant_id,
                    document_id=doc.id,
                    vector_backend=backend,
                    vector_target=target,
                    next_attempt_at=now,
                )
            )
    session.commit()  # 外部删除前持久化意图。
    jobs = session.exec(select(VectorCleanupJob).where(col(VectorCleanupJob.status) == "pending")).all()
    removed = 0
    for job in jobs:
        if job.next_attempt_at is not None and _as_utc(job.next_attempt_at) > now:
            continue
        if job.vector_backend != backend or job.vector_target != target:
            job.last_error_code = "vector_target_changed"
            session.add(job)
            session.commit()
            continue
        document = session.get(Document, job.document_id)
        if document is None or document.tenant_id != job.tenant_id:
            job.status = "failed"
            job.last_error_code = "document_identity_mismatch"
            session.add(job)
            session.commit()
            continue
        if document.deleted_at is None or _as_utc(document.deleted_at) >= cutoff:
            continue
        if job.attempt_count >= job.max_attempts:
            job.status = "failed"
            job.last_error_code = "attempts_exhausted"
            session.add(job)
            session.commit()
            continue
        job.attempt_count += 1
        session.add(job)
        session.commit()  # 崩溃后的尝试次数也能追踪。
        try:
            await get_audit_logger().log(
                action=AuditAction.KNOWLEDGE_BASE_DELETE,
                user_id=None,
                tenant_id=job.tenant_id,
                resource_type="document",
                resource_id=job.document_id,
                details={"action": "purge_attempt", "cleanup_job_id": job.id, "attempt_count": job.attempt_count},
            )
            store = get_vector_store(session)
            await store.delete_by_document(document.id, document.tenant_id)
            if document.storage_path:
                delete_source_file(document.storage_path)
            session.delete(document)
            job.status = "done"
            job.last_error_code = None
            job.next_attempt_at = None
            session.add(job)
            session.commit()
        except Exception as exc:  # noqa: BLE001 — 持久化脱敏类别用于有限重试
            session.rollback()
            job.status = "failed" if job.attempt_count >= job.max_attempts else "pending"
            job.last_error_code = type(exc).__name__
            job.next_attempt_at = now + timedelta(seconds=60 * 2 ** (job.attempt_count - 1))
            session.add(job)
            session.commit()
            logger.warning(
                "rag_cleanup_failed job=%s error_code=%s attempts=%s", job.id, job.last_error_code, job.attempt_count
            )
            continue
        removed += 1
        await get_audit_logger().log(
            action=AuditAction.KNOWLEDGE_BASE_DELETE,
            user_id=None,
            tenant_id=job.tenant_id,
            resource_type="document",
            resource_id=job.document_id,
            details={
                "tenant_id": job.tenant_id,
                "document_id": job.document_id,
                "action": "purge",
                "cleanup_job_id": job.id,
            },
        )
    return removed


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.max.replace(tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
