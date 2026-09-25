"""系统管理员的租户创建与列表。"""

from sqlmodel import Session, select

from app.models.user import Tenant


class TenantNameTakenError(Exception):
    """未停用租户中已有同名记录。"""


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


def list_active_tenants(session: Session) -> list[Tenant]:
    """返回未停用租户，按名称排序。"""
    rows = session.exec(
        select(Tenant).where(Tenant.is_active.is_(True)).order_by(Tenant.name)
    ).all()
    return list(rows)
