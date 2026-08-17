"""认证与权限核心。

职责：
- 密码哈希：bcrypt；
- JWT：access + refresh 双令牌，refresh 携带 ``token_version`` 以支持撤销；
- 角色：五级（系统管理员 / 系统访客 / 租户管理员 / 成员 / 访客）；
- 权限矩阵 + ``check_permission`` 纯函数校验。

本模块保持「纯逻辑」，不依赖 FastAPI，便于在路由依赖与测试中复用。
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum

import bcrypt
import jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_JWT_SECRET = "change-me-in-production-use-a-random-secret"

# 统一从配置读取密钥与算法，避免多处重复定义。
JWT_SECRET_KEY: str = settings.JWT_SECRET_KEY
JWT_ALGORITHM: str = settings.JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES: int = settings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS: int = settings.REFRESH_TOKEN_EXPIRE_DAYS


class Role(str, Enum):
    """五级角色（按权限从高到低）。"""

    SYSTEM_ADMIN = "system_admin"  # 平台超级管理员，跨租户
    SYSTEM_VIEWER = "system_viewer"  # 平台只读审计
    TENANT_ADMIN = "tenant_admin"  # 租户管理员
    MEMBER = "member"  # 普通成员，可读写业务数据
    VIEWER = "viewer"  # 只读访客


# ── 密码哈希 ──────────────────────────────────────────


def hash_password(password: str) -> str:
    """生成密码的 bcrypt 哈希。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验明文密码与哈希是否匹配。"""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


# ── JWT Token ─────────────────────────────────────────


def create_access_token(
    user_id: str,
    tenant_id: str,
    role: str,
    extra: dict | None = None,
) -> str:
    """创建短期 access_token。"""
    expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "type": "access",
        "jti": uuid.uuid4().hex,
        "iat": datetime.now(UTC),
        "exp": expire,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str, token_version: int = 0) -> str:
    """创建长期 refresh_token。

    将 ``token_version`` 写入载荷 ``tv`` 字段，供撤销检测。
    """
    expire = datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "tv": token_version,
        "iat": datetime.now(UTC),
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """解码并校验 JWT，返回 payload；失败时抛出 ``jwt.PyJWTError``。"""
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])


class TokenRevokedError(Exception):
    """Refresh Token 已被撤销（token_version 不匹配）。"""


def verify_refresh_token(token: str, expected_token_version: int) -> dict:
    """解码并校验 refresh_token，并通过 token_version 检测撤销。

    Args:
        token: refresh_token 字符串。
        expected_token_version: 当前用户的 token_version，用于比对。

    Returns:
        解码后的 payload。

    Raises:
        jwt.PyJWTError: 令牌无效或已过期。
        TokenRevokedError: tv 不匹配（已被撤销）。
    """
    payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    if payload.get("type") != "refresh":
        raise TokenRevokedError("需要 refresh_token")
    if payload.get("tv") != expected_token_version:
        raise TokenRevokedError("Refresh Token 已被撤销，请重新登录")
    return payload


def validate_jwt_secret() -> bool:
    """校验 JWT 密钥是否已安全配置。

    生产环境（ENV=production）下使用默认密钥会导致所有 Token 可被伪造，
    必须显式配置 ``JWT_SECRET_KEY``。

    Returns:
        True 表示密钥已正确配置；False 表示使用了默认值（仅开发环境允许）。
    """
    if JWT_SECRET_KEY == DEFAULT_JWT_SECRET:
        if not settings.is_development:
            logger.critical(
                "[安全警告] JWT_SECRET_KEY 使用了默认值！生产环境禁止启动，"
                "请通过环境变量设置一个足够随机的密钥。"
            )
            return False
        logger.warning("JWT_SECRET_KEY 使用默认值（开发环境），生产环境请务必配置安全密钥。")
    return True


# ── 权限矩阵 ──────────────────────────────────────────
# 格式：{resource: {action: [allowed_roles]}}
ROLE_PERMISSIONS: dict[str, dict[str, list[str]]] = {
    "system": {
        "read": [Role.SYSTEM_ADMIN.value, Role.SYSTEM_VIEWER.value],
        "write": [Role.SYSTEM_ADMIN.value],
    },
    "tenants": {
        "read": [Role.SYSTEM_ADMIN.value, Role.SYSTEM_VIEWER.value, Role.TENANT_ADMIN.value],
        "write": [Role.SYSTEM_ADMIN.value],
        "delete": [Role.SYSTEM_ADMIN.value],
    },
    "members": {
        "read": [Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value, Role.MEMBER.value],
        "write": [Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value],
        "delete": [Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value],
    },
    "conversations": {
        "read": [
            Role.SYSTEM_ADMIN.value,
            Role.SYSTEM_VIEWER.value,
            Role.TENANT_ADMIN.value,
            Role.MEMBER.value,
            Role.VIEWER.value,
        ],
        "write": [Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value, Role.MEMBER.value],
        "delete": [Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value, Role.MEMBER.value],
    },
    "knowledge_bases": {
        "read": [
            Role.SYSTEM_ADMIN.value,
            Role.SYSTEM_VIEWER.value,
            Role.TENANT_ADMIN.value,
            Role.MEMBER.value,
            Role.VIEWER.value,
        ],
        "write": [Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value, Role.MEMBER.value],
        "delete": [Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value],
    },
    "agents": {
        "read": [
            Role.SYSTEM_ADMIN.value,
            Role.SYSTEM_VIEWER.value,
            Role.TENANT_ADMIN.value,
            Role.MEMBER.value,
        ],
        "write": [Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value],
        "delete": [Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value],
    },
    "workflows": {
        "read": [
            Role.SYSTEM_ADMIN.value,
            Role.SYSTEM_VIEWER.value,
            Role.TENANT_ADMIN.value,
            Role.MEMBER.value,
        ],
        "write": [Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value],
        "delete": [Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value],
    },
}


def check_permission(role: str, resource: str, action: str) -> bool:
    """检查角色是否有权限执行指定操作。"""
    resource_perms = ROLE_PERMISSIONS.get(resource, {})
    allowed_roles = resource_perms.get(action, [])
    return role in allowed_roles
