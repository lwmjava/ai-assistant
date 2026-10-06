"""系统管理员列出用户、修改角色和停用用户。"""

from sqlmodel import Session, col, func, select

from app.core.security import Role
from app.models.membership import Membership
from app.models.user import Tenant, User
from app.services.membership import membership_role_for_user

_TENANT_ROLES = {Role.MEMBER.value, Role.VIEWER.value, Role.TENANT_ADMIN.value}


class AdminUserNotFoundError(Exception):
    """用户不存在。"""


class CannotChangeSelfError(Exception):
    """不能修改自己的角色。"""


class CannotDisableSelfError(Exception):
    """不能停用自己。"""


class UserAlreadyInactiveError(Exception):
    """用户已经停用。"""


class UserAlreadyActiveError(Exception):
    """用户已经启用。"""


def list_users(
    session: Session,
    *,
    page: int,
    page_size: int,
    username: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    tenant_id: str | None = None,
) -> tuple[list[tuple[User, str]], int]:
    """按用户名排序分页。返回用户、租户名称和总数。"""
    filters = []
    if username:
        filters.append(func.lower(User.username).contains(username.lower()))
    if role:
        filters.append(col(User.role) == role)
    if is_active is not None:
        filters.append(col(User.is_active).is_(is_active))
    if tenant_id:
        filters.append(col(User.tenant_id) == tenant_id)

    count_stmt = select(func.count()).select_from(User)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = int(session.exec(count_stmt).one())

    stmt = select(User, Tenant.name).join(Tenant, col(Tenant.id) == User.tenant_id)
    if filters:
        stmt = stmt.where(*filters)
    rows = session.exec(
        stmt.order_by(User.username).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [(user, tenant_name) for user, tenant_name in rows], total


def change_role(session: Session, *, actor_id: str, user_id: str, role: Role) -> tuple[User, str]:
    """修改目标用户的系统角色。租户内角色同步到他当前租户的成员关系。"""
    if actor_id == user_id:
        raise CannotChangeSelfError(user_id)
    user = session.get(User, user_id)
    if user is None:
        raise AdminUserNotFoundError(user_id)
    previous = user.role
    user.role = role.value
    session.add(user)
    if role.value in _TENANT_ROLES:
        _set_current_membership_role(session, user)
    session.commit()
    session.refresh(user)
    return user, previous


def disable_user(session: Session, *, actor_id: str, user_id: str) -> User:
    """停用用户并废除已发出的刷新令牌。访问令牌在下一次请求被拒绝。"""
    if actor_id == user_id:
        raise CannotDisableSelfError(user_id)
    user = session.get(User, user_id)
    if user is None:
        raise AdminUserNotFoundError(user_id)
    if not user.is_active:
        raise UserAlreadyInactiveError(user_id)
    user.is_active = False
    user.token_version += 1
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def enable_user(session: Session, *, user_id: str) -> User:
    """重新启用用户。不恢复停用时作废的令牌，需要重新登录。"""
    user = session.get(User, user_id)
    if user is None:
        raise AdminUserNotFoundError(user_id)
    if user.is_active:
        raise UserAlreadyActiveError(user_id)
    user.is_active = True
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _set_current_membership_role(session: Session, user: User) -> None:
    """把当前租户的成员角色改成用户角色。没有记录时补一条。"""
    row = session.exec(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.tenant_id == user.tenant_id,
        )
    ).first()
    if row is None:
        row = Membership(
            user_id=user.id,
            tenant_id=user.tenant_id,
            role=membership_role_for_user(user),
        )
    else:
        row.role = user.role
    session.add(row)
