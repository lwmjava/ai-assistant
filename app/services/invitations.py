"""邀请码的创建、列出与接受。接受只增加成员关系。"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, select

from app.core.security import Role
from app.models.membership import Invitation, Membership
from app.models.user import Tenant, User
from app.services.membership import get_membership

INVITE_DAYS = 7
INVITE_MAX_USES = 1
INVITE_ROLE = Role.MEMBER.value


class InviteForbiddenError(Exception):
    """当前用户不能为该租户发放邀请码。"""


class InviteTenantNotFoundError(Exception):
    """租户不存在。"""


class InviteTenantInactiveError(Exception):
    """租户已停用。"""


class InvitationNotFoundError(Exception):
    """邀请码不存在。"""


class InvitationExpiredError(Exception):
    """邀请码已过期。"""


class InvitationExhaustedError(Exception):
    """邀请码次数已用尽。"""


class AlreadyMemberError(Exception):
    """用户已经是该租户成员。"""


def _as_utc(value: datetime) -> datetime:
    """把数据库读出的时间统一成 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require_tenant(session: Session, tenant_id: str) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise InviteTenantNotFoundError(tenant_id)
    if not tenant.is_active:
        raise InviteTenantInactiveError(tenant_id)
    return tenant


def resolve_invite_tenant(session: Session, user: User, tenant_id: str | None) -> Tenant:
    """系统管理员必须指定租户。租户管理员只能给当前租户发码。"""
    if user.role_enum == Role.SYSTEM_ADMIN:
        if not tenant_id:
            raise InviteForbiddenError("missing-tenant")
        return _require_tenant(session, tenant_id)
    target = tenant_id or user.tenant_id
    if target != user.tenant_id:
        raise InviteForbiddenError(target)
    membership = get_membership(session, user.id, user.tenant_id)
    if membership is None or membership.role != Role.TENANT_ADMIN.value:
        raise InviteForbiddenError(user.tenant_id)
    return _require_tenant(session, user.tenant_id)


def create_invitation(session: Session, user: User, tenant_id: str | None) -> Invitation:
    """生成 7 天、单次、角色为 member 的邀请码。"""
    tenant = resolve_invite_tenant(session, user, tenant_id)
    code = secrets.token_urlsafe(16)
    row = Invitation(
        code=code,
        tenant_id=tenant.id,
        role=INVITE_ROLE,
        created_by=user.id,
        expires_at=datetime.now(UTC) + timedelta(days=INVITE_DAYS),
        max_uses=INVITE_MAX_USES,
        use_count=0,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_invitations(session: Session, user: User, tenant_id: str | None) -> list[Invitation]:
    """列出发码人可见的邀请码，包含邀请码本身。"""
    tenant = resolve_invite_tenant(session, user, tenant_id)
    rows = session.exec(
        select(Invitation).where(Invitation.tenant_id == tenant.id).order_by(col(Invitation.created_at).desc())
    ).all()
    return list(rows)


def accept_invitation(session: Session, user: User, code: str) -> tuple[Membership, Invitation]:
    """凭码加入租户。不修改当前会话租户。已是成员时不增加使用次数。"""
    invitation = session.exec(select(Invitation).where(Invitation.code == code.strip())).first()
    if invitation is None:
        raise InvitationNotFoundError(code)
    tenant = session.get(Tenant, invitation.tenant_id)
    if tenant is None or not tenant.is_active:
        raise InviteTenantInactiveError(invitation.tenant_id)
    if _as_utc(invitation.expires_at) <= datetime.now(UTC):
        raise InvitationExpiredError(invitation.id)
    existing = get_membership(session, user.id, invitation.tenant_id)
    if existing is not None:
        raise AlreadyMemberError(invitation.tenant_id)
    if invitation.use_count >= invitation.max_uses:
        raise InvitationExhaustedError(invitation.id)
    invitation.use_count += 1
    membership = Membership(
        user_id=user.id,
        tenant_id=invitation.tenant_id,
        role=INVITE_ROLE,
    )
    session.add(invitation)
    session.add(membership)
    session.commit()
    session.refresh(membership)
    session.refresh(invitation)
    return membership, invitation
