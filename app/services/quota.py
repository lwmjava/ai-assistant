"""租户消息条数与源文件字节配额。

未设置的上限是空值，表示不限制。检查和写入放在锁住该租户行的同一事务里。
"""

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import update
from sqlmodel import Session, col, func, select

from app.audit.models import AuditAction, AuditLog
from app.core.config import settings
from app.models.conversation import Conversation, Message
from app.models.rag import Document, ImportJob
from app.models.user import Tenant

logger = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    """新增会超过上限。响应里只有本维度的已用量和上限。"""

    def __init__(self, *, limit_type: str, used: int, limit: int) -> None:
        self.limit_type = limit_type
        self.used = used
        self.limit = limit
        super().__init__(limit_type)

    def as_dict(self) -> dict[str, str | int]:
        return {
            "code": "quota_exceeded",
            "limit_type": self.limit_type,
            "used": self.used,
            "limit": self.limit,
        }


class SourceFileUnreadableError(Exception):
    """已有源文件路径在，但读取大小失败。不能按 0 放行。"""


class QuotaNotFoundError(Exception):
    """目标租户不存在。"""


class QuotaAuditError(Exception):
    """审计关闭或写入失败。配额保持原值。"""


def _end_read_transaction(session: Session) -> None:
    """结束请求里已经开始的只读事务，让下一条写入成为新事务的第一条语句。"""
    previous = session.expire_on_commit
    session.expire_on_commit = False
    session.commit()
    session.expire_on_commit = previous


def _lock_tenant(session: Session, tenant_id: str) -> Tenant | None:
    """更新租户行以取得写锁。租户不存在时返回空。"""
    now = datetime.now(UTC)
    result = session.execute(update(Tenant).where(col(Tenant.id) == tenant_id).values(updated_at=now))
    if int(getattr(result, "rowcount", 0) or 0) == 0:
        return None
    return session.get(Tenant, tenant_id)


def count_user_messages(session: Session, tenant_id: str) -> int:
    """本租户现存用户消息条数。助手回复不计入。"""
    statement = (
        select(func.count())
        .select_from(Message)
        .join(Conversation, col(Message.conversation_id) == Conversation.id)
        .where(col(Conversation.tenant_id) == tenant_id, col(Message.role) == "user")
    )
    return int(session.exec(statement).one())


def source_file_size(relative_path: str) -> int | None:
    """源文件还在时返回字节数。文件已不在时返回空。其他读取失败则拒绝。"""
    from app.rag.document_storage import resolve_source_file_path

    try:
        path = resolve_source_file_path(relative_path)
        return path.stat().st_size
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SourceFileUnreadableError(relative_path) from exc


def used_source_bytes(session: Session, tenant_id: str) -> int:
    """还在磁盘上的源文件字节。软删除文档的文件仍计入。同一路径只计一次。"""
    paths: set[str] = set()
    documents = session.exec(select(Document).where(col(Document.tenant_id) == tenant_id)).all()
    for document in documents:
        if document.storage_path:
            paths.add(document.storage_path)
    jobs = session.exec(select(ImportJob).where(col(ImportJob.tenant_id) == tenant_id)).all()
    for job in jobs:
        if job.storage_path:
            paths.add(job.storage_path)
    total = 0
    for relative_path in paths:
        size = source_file_size(relative_path)
        if size is not None:
            total += size
    return total


def accept_user_message(
    session: Session,
    *,
    tenant_id: str,
    user_id: str,
    role: str,
    conversation_id: str | None,
    message: str,
) -> Conversation:
    """在锁住的租户事务里写入一条用户消息。超限时回滚，不留下新会话。"""
    _end_read_transaction(session)
    tenant = _lock_tenant(session, tenant_id)
    limit = None if tenant is None else tenant.message_limit
    if limit is None:
        if session.in_transaction():
            session.rollback()
    else:
        used = count_user_messages(session, tenant_id)
        if used >= limit:
            session.rollback()
            raise QuotaExceededError(limit_type="messages", used=used, limit=limit)

    if conversation_id:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None or not _can_access(conversation, user_id, tenant_id, role):
            session.rollback()
            raise ValueError("会话不存在或无权访问")
    else:
        conversation = Conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            title=(message or "新会话")[:40],
        )
        session.add(conversation)
        session.flush()
    session.add(Message(conversation_id=conversation.id, role="user", content=message))
    session.commit()
    session.refresh(conversation)
    return conversation


def _can_access(conversation: Conversation, user_id: str, tenant_id: str, role: str) -> bool:
    if role == "system_admin":
        return conversation.tenant_id == tenant_id
    return conversation.user_id == user_id and conversation.tenant_id == tenant_id


def begin_source_quota(session: Session, tenant_id: str, additional: int) -> bool:
    """上限为空时不占锁。有上限且放行时，调用方要在同一事务里提交新增占用。"""
    _end_read_transaction(session)
    tenant = _lock_tenant(session, tenant_id)
    if tenant is None or tenant.storage_limit_bytes is None:
        if session.in_transaction():
            session.rollback()
        return False
    limit = tenant.storage_limit_bytes
    try:
        used = used_source_bytes(session, tenant_id)
    except SourceFileUnreadableError:
        session.rollback()
        raise
    if used + additional > limit:
        session.rollback()
        raise QuotaExceededError(limit_type="source_bytes", used=used, limit=limit)
    return True


def update_tenant_quota(
    session: Session,
    *,
    actor_id: str,
    actor_tenant_id: str,
    tenant_id: str,
    message_limit: int | None,
    storage_limit_bytes: int | None,
    reason: str,
) -> Tenant:
    """修改上限，并与 quota_update 审计同一事务提交。审计失败则上限不变。"""
    if not settings.AUDIT_ENABLED:
        raise QuotaAuditError()
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise QuotaNotFoundError()
    old_message = tenant.message_limit
    old_storage = tenant.storage_limit_bytes
    try:
        tenant.message_limit = message_limit
        tenant.storage_limit_bytes = storage_limit_bytes
        session.add(tenant)
        session.add(
            AuditLog(
                user_id=actor_id,
                tenant_id=actor_tenant_id,
                action=AuditAction.QUOTA_UPDATE.value,
                resource_type="tenant",
                resource_id=tenant_id,
                details=json.dumps(
                    {
                        "actor_id": actor_id,
                        "target_tenant_id": tenant_id,
                        "message_limit_old": old_message,
                        "message_limit_new": message_limit,
                        "storage_limit_bytes_old": old_storage,
                        "storage_limit_bytes_new": storage_limit_bytes,
                        "reason": reason,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.info("配额审计写入失败，已回滚 tenant=%s", tenant_id)
        raise QuotaAuditError() from exc
    session.refresh(tenant)
    return tenant
