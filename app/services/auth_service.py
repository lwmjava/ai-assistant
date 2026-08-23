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
