"""认证业务服务。

封装用户认证、创建与初始管理员引导逻辑，供路由层调用。
"""

import logging

from sqlmodel import Session, select

from app.core.config import settings
from app.core.security import Role, hash_password, verify_password
from app.models.user import Tenant, User

logger = logging.getLogger(__name__)


def authenticate(session: Session, username: str, password: str) -> User | None:
    """校验用户名与密码，成功返回 User，失败返回 None。"""
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


class UsernameTakenError(Exception):
    """用户名已被占用。"""


class EmailTakenError(Exception):
    """邮箱已被占用。"""


class DefaultTenantInactiveError(Exception):
    """名为 default 的租户已停用。"""


class SetupClosedError(Exception):
    """已经存在系统管理员，初始化向导不再接受创建。"""


class UserNotFoundError(Exception):
    """撤销目标用户不存在。"""


DEFAULT_TENANT_NAME = "default"


def create_user(
    session: Session,
    *,
    tenant_id: str,
    username: str,
    password: str,
    email: str | None = None,
    role: Role = Role.MEMBER,
) -> User:
    """在指定租户下创建用户（调用方需自行校验权限）。"""
    user = User(
        tenant_id=tenant_id,
        username=username,
        email=email,
        hashed_password=hash_password(password),
        role=role.value,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _reject_taken_identity(session: Session, username: str, email: str | None) -> None:
    """用户名全局唯一；填写了邮箱时邮箱也全局唯一。"""
    taken_name = session.exec(select(User).where(User.username == username)).first()
    if taken_name is not None:
        raise UsernameTakenError(username)
    if email:
        taken_email = session.exec(select(User).where(User.email == email)).first()
        if taken_email is not None:
            raise EmailTakenError(email)


def get_or_create_default_tenant(session: Session) -> Tenant:
    """返回仍启用的 default 租户。没有就创建。已停用则拒绝，不另建同名租户。"""
    tenant = session.exec(select(Tenant).where(Tenant.name == DEFAULT_TENANT_NAME)).first()
    if tenant is None:
        tenant = Tenant(name=DEFAULT_TENANT_NAME)
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        return tenant
    if not tenant.is_active:
        raise DefaultTenantInactiveError(tenant.id)
    return tenant


def register_member(
    session: Session,
    *,
    username: str,
    password: str,
    email: str | None = None,
) -> User:
    """公开注册：加入 default 租户，角色固定为 member。"""
    tenant = get_or_create_default_tenant(session)
    _reject_taken_identity(session, username, email)
    return create_user(
        session,
        tenant_id=tenant.id,
        username=username,
        password=password,
        email=email,
        role=Role.MEMBER,
    )


def system_admin_exists(session: Session) -> bool:
    """库中是否已有系统管理员。停用的管理员也算已初始化。"""
    row = session.exec(select(User).where(User.role == Role.SYSTEM_ADMIN.value)).first()
    return row is not None


def setup_system_admin(
    session: Session,
    *,
    username: str,
    password: str,
    email: str | None = None,
) -> User:
    """没有系统管理员时，在 default 租户创建首个 system_admin。"""
    if system_admin_exists(session):
        raise SetupClosedError()
    tenant = get_or_create_default_tenant(session)
    _reject_taken_identity(session, username, email)
    return create_user(
        session,
        tenant_id=tenant.id,
        username=username,
        password=password,
        email=email,
        role=Role.SYSTEM_ADMIN,
    )


def revoke_refresh_tokens(session: Session, user_id: str) -> User:
    """递增 token_version，使该用户已发出的刷新令牌失效。"""
    user = session.get(User, user_id)
    if user is None:
        raise UserNotFoundError(user_id)
    user.token_version += 1
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def ensure_initial_admin(session: Session) -> None:
    """首次引导：若库中无用户，按环境变量创建 system_admin。

    仅当 ``INITIAL_ADMIN_USERNAME`` 与 ``INITIAL_ADMIN_PASSWORD`` 均已配置时生效；
    否则跳过（由运维手动创建首个账号）。
    """
    existing = session.exec(select(User)).first()
    if existing is not None:
        return

    username = settings.INITIAL_ADMIN_USERNAME
    password = settings.INITIAL_ADMIN_PASSWORD
    if not username or not password:
        logger.info("未配置 INITIAL_ADMIN_USERNAME/PASSWORD，跳过初始管理员引导，请手动创建首个账号。")
        return

    # 引导用租户（首个租户）。
    tenant = session.exec(select(Tenant).where(Tenant.name == "default")).first()
    if tenant is None:
        tenant = Tenant(name="default")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)

    admin = User(
        tenant_id=tenant.id,
        username=username,
        email=settings.INITIAL_ADMIN_EMAIL or None,
        hashed_password=hash_password(password),
        role=Role.SYSTEM_ADMIN.value,
    )
    session.add(admin)
    session.commit()
    logger.warning(
        "已创建初始 system_admin 账号 '%s'（来源：环境变量）。"
        "出于安全考虑，请在创建后修改密码或移除相关环境变量。",
        username,
    )
