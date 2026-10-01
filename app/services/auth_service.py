"""认证业务服务。

封装用户认证、创建与初始管理员引导逻辑，供路由层调用。
"""

import logging
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import NullPool
from sqlmodel import Session, col, create_engine, func, select

from app.core.config import settings
from app.core.security import Role, hash_password, verify_password
from app.models.membership import Membership
from app.models.user import Tenant, User
from app.services.membership import (
    backfill_memberships,
    ensure_membership,
    membership_role_for_user,
)

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


class PasswordTooShortError(Exception):
    """密码短于 8 位，未写入用户。"""


class DatabaseNotMigratedError(Exception):
    """用户表还不存在，不能创建系统管理员。"""


MIN_CLI_PASSWORD_LENGTH = 8
_CLI_ADMIN_LOCK_KEY = 872001


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
    ensure_membership(session, user)
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


def _ensure_sqlite_parent(database_url: str) -> None:
    """相对或绝对的 SQLite 文件路径，父目录不存在时创建。"""
    if not database_url.startswith("sqlite") or ":memory:" in database_url:
        return
    raw = database_url.split("///", 1)[-1]
    parent = Path(raw).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


def _engine_for_admin_create(database_url: str):
    """命令行创建管理员用的引擎。

    SQLite 在事务开始时发出 BEGIN IMMEDIATE，让重叠的两次创建串行执行。
    不给系统管理员角色加唯一约束，管理接口仍可创建多名管理员。
    """
    _ensure_sqlite_parent(database_url)
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30
    engine = create_engine(database_url, connect_args=connect_args, poolclass=NullPool)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _disable_pysqlite_begin(dbapi_connection, _connection_record) -> None:
            dbapi_connection.isolation_level = None

        @event.listens_for(engine, "begin")
        def _begin_immediate(connection) -> None:
            connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def _lock_admin_create(session: Session) -> None:
    """PostgreSQL 用事务级咨询锁串行创建。SQLite 由 BEGIN IMMEDIATE 负责。"""
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CLI_ADMIN_LOCK_KEY})


def _default_tenant_for_admin(session: Session) -> Tenant:
    """在当前事务里取得仍启用的 default 租户，没有就创建。"""
    tenant = session.exec(select(Tenant).where(col(Tenant.name) == DEFAULT_TENANT_NAME)).first()
    if tenant is None:
        tenant = Tenant(name=DEFAULT_TENANT_NAME)
        session.add(tenant)
        session.flush()
        return tenant
    if not tenant.is_active:
        raise DefaultTenantInactiveError(tenant.id)
    return tenant


def create_cli_superuser(*, username: str, password: str) -> User:
    """在一个事务里创建系统管理员。

    写入前统计已有 system_admin，人数大于 0 则拒绝。用户名冲突拒绝。
    密码短于 8 位时不写用户。日志只记录用户名。
    """
    if len(password) < MIN_CLI_PASSWORD_LENGTH:
        raise PasswordTooShortError()
    hashed = hash_password(password)
    engine = _engine_for_admin_create(settings.DATABASE_URL)
    try:
        # 成员表由启动时的建表补上，迁移脚本里没有。这里补齐后再写入同一事务。
        Membership.__table__.create(engine, checkfirst=True)
        try:
            with Session(engine) as session:
                _lock_admin_create(session)
                admin_count = int(
                    session.exec(
                        select(func.count())
                        .select_from(User)
                        .where(col(User.role) == Role.SYSTEM_ADMIN.value)
                    ).one()
                )
                if admin_count > 0:
                    raise SetupClosedError()
                tenant = _default_tenant_for_admin(session)
                taken = session.exec(select(User).where(col(User.username) == username)).first()
                if taken is not None:
                    raise UsernameTakenError(username)
                user = User(
                    tenant_id=tenant.id,
                    username=username,
                    email=None,
                    hashed_password=hashed,
                    role=Role.SYSTEM_ADMIN.value,
                )
                session.add(user)
                try:
                    session.flush()
                    session.add(
                        Membership(
                            user_id=user.id,
                            tenant_id=tenant.id,
                            role=membership_role_for_user(user),
                        )
                    )
                    session.commit()
                except IntegrityError as exc:
                    session.rollback()
                    raise UsernameTakenError(username) from exc
                logger.info("已创建系统管理员 '%s'", username)
                return user
        except OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            raise DatabaseNotMigratedError() from exc
    finally:
        engine.dispose()


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
    backfill_memberships(session)
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
    session.refresh(admin)
    ensure_membership(session, admin)
    logger.warning(
        "已创建初始 system_admin 账号 '%s'（来源：环境变量）。"
        "出于安全考虑，请在创建后修改密码或移除相关环境变量。",
        username,
    )
