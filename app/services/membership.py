"""成员关系：补齐现有用户，并在已加入后切换当前租户。"""

from sqlmodel import Session, select

from app.core.security import Role
from app.models.membership import Membership
from app.models.user import Tenant, User

_TENANT_ROLES = {Role.MEMBER.value, Role.VIEWER.value, Role.TENANT_ADMIN.value}


class NotMemberError(Exception):
    """当前用户不是目标租户的成员。"""


def membership_role_for_user(user: User) -> str:
    """系统角色不写入成员表。其余角色按用户当前角色补一条。"""
    if user.role in _TENANT_ROLES:
        return user.role
    return Role.MEMBER.value


def get_membership(session: Session, user_id: str, tenant_id: str) -> Membership | None:
    """返回用户在指定租户的成员关系。"""
    return session.exec(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.tenant_id == tenant_id,
        )
    ).first()


def ensure_membership(session: Session, user: User) -> Membership:
    """保证用户在当前 tenant_id 上有一条成员关系。已有则不改角色。"""
    existing = get_membership(session, user.id, user.tenant_id)
    if existing is not None:
        return existing
    row = Membership(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=membership_role_for_user(user),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def backfill_memberships(session: Session) -> None:
    """为还没有当前租户成员关系的用户补一条。"""
    users = session.exec(select(User)).all()
    for user in users:
        ensure_membership(session, user)


def list_memberships(session: Session, user: User) -> list[tuple[Membership, Tenant]]:
    """返回当前用户的成员关系及租户名称。"""
    rows = session.exec(select(Membership).where(Membership.user_id == user.id)).all()
    result: list[tuple[Membership, Tenant]] = []
    for row in rows:
        tenant = session.get(Tenant, row.tenant_id)
        if tenant is not None:
            result.append((row, tenant))
    return result


def switch_tenant(session: Session, user: User, tenant_id: str) -> User:
    """是成员才改当前租户并递增 token_version。不是成员时不写任何字段。"""
    if get_membership(session, user.id, tenant_id) is None:
        raise NotMemberError(tenant_id)
    user.tenant_id = tenant_id
    user.token_version += 1
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
