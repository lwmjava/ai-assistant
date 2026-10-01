"""软删文档的保留期清理。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.audit.logger import get_audit_logger
from app.audit.models import AuditAction
from app.models.rag import Document
from app.rag.document_storage import delete_source_file
from app.rag.vectorstore.factory import get_vector_store

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90


async def purge_expired_documents(session: Session, *, now: datetime) -> int:
    """物理删除已超过保留期的软删文档。未到期的行保持不动。"""
    cutoff = now - timedelta(days=RETENTION_DAYS)
    rows = session.exec(select(Document).where(Document.deleted_at.is_not(None))).all()
    expired = [row for row in rows if _as_utc(row.deleted_at) < cutoff]
    removed = 0
    store = get_vector_store(session)
    for doc in expired:
        try:
            await store.delete_by_document(doc.id, doc.tenant_id)
        except Exception:  # noqa: BLE001 — 向量清理失败不留下无法删除的行
            logger.exception("清理过期文档向量失败: document=%s", doc.id)
        if doc.storage_path:
            delete_source_file(doc.storage_path)
        document_id = doc.id
        tenant_id = doc.tenant_id
        session.delete(doc)
        session.commit()
        await get_audit_logger().log(
            action=AuditAction.KNOWLEDGE_BASE_DELETE,
            user_id=None,
            tenant_id=tenant_id,
            resource_type="document",
            resource_id=document_id,
            details={
                "tenant_id": tenant_id,
                "document_id": document_id,
                "action": "purge",
            },
        )
        removed += 1
    return removed


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.max.replace(tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
