"""系统管理员的租户创建与列表。"""

from sqlmodel import Session, select

from app.core.security import Role
from app.models.user import Tenant, User
from app.services.auth_service import create_user


class TenantNameTakenError(Exception):
    """未停用租户中已有同名记录。"""


class TenantNotFoundError(Exception):
    """租户不存在。"""


class TenantInactiveError(Exception):
    """租户已停用。"""


class UsernameTakenError(Exception):
    """用户名已被占用。"""


class EmailTakenError(Exception):
    """邮箱已被占用。"""


def create_tenant(session: Session, *, name: str) -> Tenant:
    """创建未停用租户。同名且仍启用的租户已存在时拒绝。"""
    existing = session.exec(
        select(Tenant).where(Tenant.name == name, Tenant.is_active.is_(True))
    ).first()
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
    rows = session.exec(
        select(Tenant).where(Tenant.is_active.is_(True)).order_by(Tenant.name)
    ).all()
    return list(rows)
