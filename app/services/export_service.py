"""把当前租户的对话导出为固定 JSON。

只看令牌租户上的成员角色。先提交审计，调用方再开始写出响应。
"""

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import and_, or_
from sqlmodel import Session, col, select

from app.audit.models import AuditAction, AuditLog
from app.core.config import settings
from app.core.security import Role
from app.models.conversation import Conversation, Message
from app.services.membership import get_membership

logger = logging.getLogger(__name__)

MAX_EXPORT_MESSAGES = 5000
MAX_EXPORT_BYTES = 32 * 1024 * 1024
_PAGE_SIZE = 200


class ExportForbiddenError(Exception):
    """当前租户的成员关系不是租户管理员。"""


class ExportTooLargeError(Exception):
    """条数或字节超过单次上限。不携带消息正文。"""


class ExportAuditError(Exception):
    """审计关闭或写入失败。不携带消息正文。"""


def iter_export_bytes(raw: bytes) -> Iterator[bytes]:
    """审计提交之后才被拉取，响应体从这里开始写出。"""
    yield raw


def export_tenant_conversations(session: Session, *, user_id: str, tenant_id: str) -> bytes:
    """组装本租户对话。审计提交成功后才把字节交给调用方。"""
    membership = get_membership(session, user_id, tenant_id)
    if membership is None or membership.role != Role.TENANT_ADMIN.value:
        raise ExportForbiddenError()
    if not settings.AUDIT_ENABLED:
        raise ExportAuditError()
    try:
        raw, conversation_count, message_count = _assemble(session, tenant_id)
    except ExportTooLargeError:
        session.rollback()
        logger.info("导出超过上限 tenant=%s", tenant_id)
        raise
    try:
        session.add(
            AuditLog(
                user_id=user_id,
                tenant_id=tenant_id,
                action=AuditAction.EXPORT_REQUESTED.value,
                resource_type="tenant",
                resource_id=tenant_id,
                details=json.dumps(
                    {
                        "actor_id": user_id,
                        "tenant_id": tenant_id,
                        "conversation_count": conversation_count,
                        "message_count": message_count,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.info("导出审计写入失败 tenant=%s", tenant_id)
        raise ExportAuditError() from exc
    return raw


def _assemble(session: Session, tenant_id: str) -> tuple[bytes, int, int]:
    budget = _ExportBudget()
    budget.start(tenant_id, datetime.now(UTC).isoformat())
    conversation_cursor: tuple[datetime, str] | None = None
    while True:
        conversations = _next_conversations(session, tenant_id, conversation_cursor)
        if not conversations:
            break
        for conversation in conversations:
            budget.begin_conversation(conversation.id, conversation.title)
            message_cursor: tuple[datetime, str] | None = None
            while True:
                messages = _next_messages(session, conversation.id, message_cursor)
                if not messages:
                    break
                for message in messages:
                    budget.add_message(message.role, message.content, message.created_at.isoformat())
                message_cursor = (messages[-1].created_at, messages[-1].id)
                if len(messages) < _PAGE_SIZE:
                    break
            budget.end_conversation()
        conversation_cursor = (conversations[-1].created_at, conversations[-1].id)
        if len(conversations) < _PAGE_SIZE:
            break
    return budget.finish(), budget.conversation_count, budget.message_count


def _next_conversations(
    session: Session, tenant_id: str, cursor: tuple[datetime, str] | None
) -> list[Conversation]:
    statement = select(Conversation).where(col(Conversation.tenant_id) == tenant_id)
    if cursor is not None:
        created_at, row_id = cursor
        statement = statement.where(
            or_(
                col(Conversation.created_at) > created_at,
                and_(col(Conversation.created_at) == created_at, col(Conversation.id) > row_id),
            )
        )
    statement = statement.order_by(col(Conversation.created_at).asc(), col(Conversation.id).asc()).limit(
        _PAGE_SIZE
    )
    return list(session.exec(statement).all())


def _next_messages(
    session: Session, conversation_id: str, cursor: tuple[datetime, str] | None
) -> list[Message]:
    statement = select(Message).where(col(Message.conversation_id) == conversation_id)
    if cursor is not None:
        created_at, row_id = cursor
        statement = statement.where(
            or_(
                col(Message.created_at) > created_at,
                and_(col(Message.created_at) == created_at, col(Message.id) > row_id),
            )
        )
    statement = statement.order_by(col(Message.created_at).asc(), col(Message.id).asc()).limit(_PAGE_SIZE)
    return list(session.exec(statement).all())


class _ExportBudget:
    """边写边数。超限时抛出，不把已写的半份交给调用方。"""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._first_conversation = True
        self._first_message = True
        self.conversation_count = 0
        self.message_count = 0

    def start(self, tenant_id: str, exported_at: str) -> None:
        self._add('{"tenant_id":')
        self._add(json.dumps(tenant_id, ensure_ascii=False))
        self._add(',"exported_at":')
        self._add(json.dumps(exported_at, ensure_ascii=False))
        self._add(',"conversations":[')

    def begin_conversation(self, conversation_id: str, title: str | None) -> None:
        if not self._first_conversation:
            self._add(",")
        self._first_conversation = False
        self._add('{"id":')
        self._add(json.dumps(conversation_id, ensure_ascii=False))
        self._add(',"title":')
        self._add(json.dumps(title, ensure_ascii=False))
        self._add(',"messages":[')
        self._first_message = True
        self.conversation_count += 1

    def add_message(self, role: str, content: str, created_at: str) -> None:
        if len(content.encode("utf-8")) > MAX_EXPORT_BYTES:
            raise ExportTooLargeError()
        if self.message_count + 1 > MAX_EXPORT_MESSAGES:
            raise ExportTooLargeError()
        piece = (
            '{"role":'
            + json.dumps(role, ensure_ascii=False)
            + ',"content":'
            + json.dumps(content, ensure_ascii=False)
            + ',"created_at":'
            + json.dumps(created_at, ensure_ascii=False)
            + "}"
        )
        encoded = piece.encode("utf-8")
        extra = len(encoded) if self._first_message else len(encoded) + 1
        if len(self._buf) + extra > MAX_EXPORT_BYTES:
            raise ExportTooLargeError()
        if not self._first_message:
            self._add(",")
        self._first_message = False
        self._add(piece)
        self.message_count += 1

    def end_conversation(self) -> None:
        self._add("]}")

    def finish(self) -> bytes:
        self._add("]}")
        return bytes(self._buf)

    def _add(self, text: str) -> None:
        encoded = text.encode("utf-8")
        if len(self._buf) + len(encoded) > MAX_EXPORT_BYTES:
            raise ExportTooLargeError()
        self._buf.extend(encoded)
