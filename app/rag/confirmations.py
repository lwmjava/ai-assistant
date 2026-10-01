"""跨租户敏感操作的一次性确认。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session

from app.core.security import Role
from app.models.rag import Document, OperationConfirmation
from app.models.user import User

CONFIRMATION_ACTIONS = frozenset({"delete", "reparse", "publish"})


def needs_cross_tenant_confirmation(doc: Document, user: User) -> bool:
    """系统管理员操作其他租户的文档时需要确认。"""
    return user.role_enum == Role.SYSTEM_ADMIN and doc.tenant_id != user.tenant_id


def create_confirmation(session: Session, user: User, doc: Document, action: str) -> OperationConfirmation:
    """创建尚未消费的确认记录。"""
    if action not in CONFIRMATION_ACTIONS:
        raise ValueError("不支持的确认动作")
    if not needs_cross_tenant_confirmation(doc, user):
        raise ValueError("该操作不需要确认")
    row = OperationConfirmation(
        actor_user_id=user.id,
        tenant_id=doc.tenant_id,
        document_id=doc.id,
        action=action,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def consume_confirmation(
    session: Session,
    user: User,
    doc: Document,
    action: str,
    confirmation_id: str | None,
) -> None:
    """校验并标记确认已使用。调用方负责提交。不需要确认时直接返回。"""
    if not needs_cross_tenant_confirmation(doc, user):
        return
    if not confirmation_id:
        raise ValueError("跨租户操作需要确认")
    row = session.get(OperationConfirmation, confirmation_id)
    if (
        row is None
        or row.actor_user_id != user.id
        or row.document_id != doc.id
        or row.action != action
        or row.consumed_at is not None
    ):
        raise ValueError("确认记录无效")
    row.consumed_at = datetime.now(UTC)
    session.add(row)
