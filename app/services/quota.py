"""租户消息条数、Token 与源文件字节配额。

未设置的上限是空值，表示不限制。检查和写入放在锁住该租户行的同一事务里。
Token 按自然月（UTC）累计；跨月时清零本周期用量。
"""

from __future__ import annotations

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
    """新增会超过上限。响应里含本维度用量、上限与下次自然月重置时间。"""

    def __init__(self, *, limit_type: str, used: int, limit: int, resets_at: str) -> None:
        self.limit_type = limit_type
        self.used = used
        self.limit = limit
        self.resets_at = resets_at
        super().__init__(limit_type)

    def as_dict(self) -> dict[str, str | int]:
        return {
            "code": "quota_exceeded",
            "limit_type": self.limit_type,
            "used": self.used,
            "limit": self.limit,
            "resets_at": self.resets_at,
        }


class SourceFileUnreadableError(Exception):
    """已有源文件路径在，但读取大小失败。不能按 0 放行。"""


class QuotaNotFoundError(Exception):
    """目标租户不存在。"""


class QuotaAuditError(Exception):
    """审计关闭或写入失败。配额保持原值。"""


def current_period_ym(now: datetime | None = None) -> str:
    """UTC 自然月键，格式 YYYY-MM。"""
    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    else:
        stamp = stamp.astimezone(UTC)
    return f"{stamp.year:04d}-{stamp.month:02d}"


def next_month_resets_at(now: datetime | None = None) -> str:
    """下一自然月 UTC 起点的 ISO-8601 时间。"""
    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    else:
        stamp = stamp.astimezone(UTC)
    year = stamp.year + (1 if stamp.month == 12 else 0)
    month = 1 if stamp.month == 12 else stamp.month + 1
    return datetime(year, month, 1, tzinfo=UTC).isoformat().replace("+00:00", "Z")


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


def ensure_quota_period(tenant: Tenant, *, now: datetime | None = None) -> bool:
    """跨自然月时清零 Token 周期用量。返回是否发生了重置。"""
    period = current_period_ym(now)
    if tenant.quota_period_ym == period:
        return False
    tenant.quota_period_ym = period
    tenant.tokens_used_period = 0
    return True


def count_user_messages(session: Session, tenant_id: str) -> int:
    """本租户未软删除会话中的用户消息条数。助手回复不计入。"""
    statement = (
        select(func.count())
        .select_from(Message)
        .join(Conversation, col(Message.conversation_id) == Conversation.id)
        .where(
            col(Conversation.tenant_id) == tenant_id,
            col(Message.role) == "user",
            col(Conversation.deleted_at).is_(None),
        )
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


def estimate_turn_tokens(*, user_text: str, assistant_text: str = "") -> int:
    """无供应商 usage 时，按字符粗估本轮 Token（至少 1）。"""
    chars = len(user_text or "") + len(assistant_text or "")
    return max(1, (chars + 3) // 4)


def accept_user_message(
    session: Session,
    *,
    tenant_id: str,
    user_id: str,
    role: str,
    conversation_id: str | None,
    message: str,
    attachments: str | None = None,
) -> tuple[Conversation, str]:
    """在锁住的租户事务里写入一条用户消息。超限时回滚，不留下新会话。

    同时检查消息条数库存与本月 Token 余额；任一超限都不落库。
    返回 ``(conversation, user_message_id)``。
    """
    _end_read_transaction(session)
    tenant = _lock_tenant(session, tenant_id)
    resets_at = next_month_resets_at()
    if tenant is not None:
        ensure_quota_period(tenant)
        session.add(tenant)

    message_limit = None if tenant is None else tenant.message_limit
    token_limit = None if tenant is None else tenant.token_limit
    tokens_used = 0 if tenant is None else int(tenant.tokens_used_period or 0)

    if message_limit is not None:
        used = count_user_messages(session, tenant_id)
        if used >= message_limit:
            session.rollback()
            raise QuotaExceededError(
                limit_type="messages", used=used, limit=message_limit, resets_at=resets_at
            )
    if token_limit is not None and tokens_used >= token_limit:
        session.rollback()
        raise QuotaExceededError(
            limit_type="tokens", used=tokens_used, limit=token_limit, resets_at=resets_at
        )

    if conversation_id:
        conversation = session.get(Conversation, conversation_id)
        if (
            conversation is None
            or not _can_access(conversation, user_id, tenant_id, role)
            or conversation.deleted_at is not None
        ):
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
    user_message = Message(conversation_id=conversation.id, role="user", content=message, attachments=attachments)
    session.add(user_message)
    session.commit()
    session.refresh(conversation)
    session.refresh(user_message)
    return conversation, user_message.id


def consume_tokens(session: Session, tenant_id: str, amount: int) -> None:
    """对话成功后累加本月 Token。amount <= 0 时忽略。"""
    if amount <= 0:
        return
    _end_read_transaction(session)
    tenant = _lock_tenant(session, tenant_id)
    if tenant is None:
        if session.in_transaction():
            session.rollback()
        return
    ensure_quota_period(tenant)
    tenant.tokens_used_period = int(tenant.tokens_used_period or 0) + amount
    session.add(tenant)
    session.commit()


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
        raise QuotaExceededError(
            limit_type="source_bytes",
            used=used,
            limit=limit,
            resets_at=next_month_resets_at(),
        )
    return True


def update_tenant_quota(
    session: Session,
    *,
    actor_id: str,
    actor_tenant_id: str,
    tenant_id: str,
    message_limit: int | None,
    storage_limit_bytes: int | None,
    token_limit: int | None,
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
    old_token = tenant.token_limit
    try:
        tenant.message_limit = message_limit
        tenant.storage_limit_bytes = storage_limit_bytes
        tenant.token_limit = token_limit
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
                        "token_limit_old": old_token,
                        "token_limit_new": token_limit,
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
