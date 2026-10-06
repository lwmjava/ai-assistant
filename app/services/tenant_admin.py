"""系统管理员的租户创建与列表。"""

from sqlmodel import Session, col, select

from app.core.security import Role
from app.models.user import Tenant, User
from app.services.auth_service import EmailTakenError, UsernameTakenError, create_user

__all__ = [
    "EmailTakenError",
    "TenantInactiveError",
    "TenantNameTakenError",
    "TenantNotFoundError",
    "UsernameTakenError",
]


class TenantNameTakenError(Exception):
    """未停用租户中已有同名记录。"""


class TenantNotFoundError(Exception):
    """租户不存在。"""


class TenantInactiveError(Exception):
    """租户已停用。"""


class TenantAlreadyActiveError(Exception):
    """租户已启用。"""


def create_tenant(session: Session, *, name: str) -> Tenant:
    """创建未停用租户。同名且仍启用的租户已存在时拒绝。"""
    existing = session.exec(select(Tenant).where(Tenant.name == name, col(Tenant.is_active).is_(True))).first()
    if existing is not None:
        raise TenantNameTakenError(name)
    tenant = Tenant(name=name, is_active=True)
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return tenant


def create_member(
    session: Session,
    *,
    tenant_id: str,
    username: str,
    password: str,
    email: str | None = None,
) -> User:
    """在未停用租户下创建成员。角色固定为 member。"""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise TenantNotFoundError(tenant_id)
    if not tenant.is_active:
        raise TenantInactiveError(tenant_id)
    taken_name = session.exec(select(User).where(User.username == username)).first()
    if taken_name is not None:
        raise UsernameTakenError(username)
    if email:
        taken_email = session.exec(select(User).where(User.email == email)).first()
        if taken_email is not None:
            raise EmailTakenError(email)
    return create_user(
        session,
        tenant_id=tenant.id,
        username=username,
        password=password,
        email=email,
        role=Role.MEMBER,
    )


def list_active_tenants(session: Session) -> list[Tenant]:
    """返回未停用租户，按名称排序。"""
    return list_tenants(session, include_inactive=False)


def list_tenants(session: Session, *, include_inactive: bool) -> list[Tenant]:
    """按名称列出租户。默认不含已停用。"""
    stmt = select(Tenant)
    if not include_inactive:
        stmt = stmt.where(col(Tenant.is_active).is_(True))
    return list(session.exec(stmt.order_by(Tenant.name)).all())


def rename_tenant(session: Session, tenant_id: str, name: str) -> Tenant:
    """修改未停用租户的名称。与其他未停用租户重名时拒绝。"""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise TenantNotFoundError(tenant_id)
    if not tenant.is_active:
        raise TenantInactiveError(tenant_id)
    taken = session.exec(
        select(Tenant).where(
            Tenant.name == name,
            col(Tenant.is_active).is_(True),
            Tenant.id != tenant.id,
        )
    ).first()
    if taken is not None:
        raise TenantNameTakenError(name)
    tenant.name = name
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return tenant


def deactivate_tenant(session: Session, tenant_id: str) -> Tenant:
    """停用租户，并废除当前正在该租户中的用户的刷新令牌。"""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise TenantNotFoundError(tenant_id)
    if not tenant.is_active:
        raise TenantInactiveError(tenant_id)
    tenant.is_active = False
    session.add(tenant)
    users = session.exec(select(User).where(User.tenant_id == tenant.id)).all()
    for user in users:
        user.token_version += 1
        session.add(user)
    session.commit()
    session.refresh(tenant)
    return tenant


def activate_tenant(session: Session, tenant_id: str) -> Tenant:
    """重新启用已停用租户。与其他未停用租户重名时拒绝，不恢复已作废的令牌。"""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise TenantNotFoundError(tenant_id)
    if tenant.is_active:
        raise TenantAlreadyActiveError(tenant_id)
    taken = session.exec(
        select(Tenant).where(
            Tenant.name == tenant.name,
            col(Tenant.is_active).is_(True),
            Tenant.id != tenant.id,
        )
    ).first()
    if taken is not None:
        raise TenantNameTakenError(tenant.name)
    tenant.is_active = True
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return tenant
