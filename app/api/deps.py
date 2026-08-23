"""FastAPI 依赖：数据库会话、当前用户、权限守卫。"""

import logging
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core.config import settings
from app.core.database import get_session
from app.core.security import Role, TokenRevokedError, check_permission, decode_token
from app.models.user import User

logger = logging.getLogger(__name__)

# 不自动抛错，便于在缺失令牌时返回标准的 401。
_bearer = HTTPBearer(auto_error=False)

# 当认证关闭（AUTH_ENABLED=false，仅开发/演示）时使用的合成管理员主体。
_DISABLED_AUTH_PRINCIPAL = User(
    id="__auth_disabled__",
    tenant_id="__auth_disabled__",
    username="auth-disabled",
    hashed_password="",
    role=Role.SYSTEM_ADMIN.value,
    token_version=0,
    is_active=True,
)


# 数据库会话依赖：get_session 本身是生成器，FastAPI 按生成器依赖处理（请求结束后自动关闭）。
# 在此直接复用，便于路由统一从 deps 导入。
get_db = get_session


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> User:
    """解析 Bearer Token，返回当前用户。

    认证关闭时返回合成管理员主体（仅限开发/演示环境）。
    """
    if not settings.AUTH_ENABLED:
        return _DISABLED_AUTH_PRINCIPAL

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌类型错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    user = session.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已禁用",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_permission(resource: str, action: str) -> Callable[[User], User]:
    """权限守卫工厂（PermissionGuard）。

    用法：``current_user: User = Depends(require_permission("knowledge_bases", "write"))``

    Args:
        resource: 资源名（与 ROLE_PERMISSIONS 对齐）。
        action: 操作名（read/write/delete）。

    Returns:
        一个 FastAPI 依赖函数；权限不足时返回 403。
    """

    def _guard(current_user: User = Depends(get_current_user)) -> User:
        if not check_permission(current_user.role, resource, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：需要 {resource}:{action}",
            )
        return current_user

    return _guard
