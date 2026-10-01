"""系统管理员的用户列表、改角色与停用。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlmodel import Session

from app.api.deps import audit_event, get_current_user, get_db
from app.audit.models import AuditAction
from app.core.security import Role
from app.models.user import User
from app.schemas.admin import UserAdminOut, UserPage, UserRoleUpdate
from app.services.admin_users import (
    AdminUserNotFoundError,
    CannotChangeSelfError,
    CannotDisableSelfError,
    UserAlreadyInactiveError,
    change_role,
    disable_user,
    list_users,
)

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


def _require_system_admin(user: User = Depends(get_current_user)) -> User:
    """用户管理只对系统管理员开放。"""
    if user.role_enum is not Role.SYSTEM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅系统管理员可管理用户",
        )
    return user


@router.get("", response_model=UserPage)
def list_users_route(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    username: str | None = Query(default=None),
    role: Role | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    session: Session = Depends(get_db),
    _: User = Depends(_require_system_admin),
) -> UserPage:
    """分页列出用户，含已停用。"""
    needle = username.strip() if username else None
    rows, total = list_users(
        session,
        page=page,
        page_size=page_size,
        username=needle or None,
        role=role.value if role else None,
        is_active=is_active,
    )
    items = [
        UserAdminOut(
            id=user.id,
            tenant_id=user.tenant_id,
            tenant_name=tenant_name,
            username=user.username,
            email=user.email,
            role=Role(user.role),
            is_active=user.is_active,
        )
        for user, tenant_name in rows
    ]
    return UserPage(items=items, total=total, page=page, page_size=page_size)


@router.patch("/{user_id}", response_model=UserAdminOut)
async def change_role_route(
    user_id: str,
    body: UserRoleUpdate,
    request: Request,
    session: Session = Depends(get_db),
    current_user: User = Depends(_require_system_admin),
) -> UserAdminOut:
    """修改用户角色。不能修改自己。"""
    try:
        user, previous = change_role(
            session, actor_id=current_user.id, user_id=user_id, role=body.role
        )
    except CannotChangeSelfError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不能修改自己的角色")
    except AdminUserNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    await audit_event(
        request,
        AuditAction.USER_ROLE_CHANGE,
        user=current_user,
        resource_type="user",
        resource_id=user.id,
        details={"from": previous, "to": user.role},
    )
    tenant_name = user.tenant.name if user.tenant is not None else ""
    return UserAdminOut(
        id=user.id,
        tenant_id=user.tenant_id,
        tenant_name=tenant_name,
        username=user.username,
        email=user.email,
        role=Role(user.role),
        is_active=user.is_active,
    )


@router.post("/{user_id}/disable", response_model=UserAdminOut)
async def disable_user_route(
    user_id: str,
    request: Request,
    session: Session = Depends(get_db),
    current_user: User = Depends(_require_system_admin),
) -> UserAdminOut:
    """停用用户。该用户之后不能再访问，旧刷新令牌失效。"""
    try:
        user = disable_user(session, actor_id=current_user.id, user_id=user_id)
    except CannotDisableSelfError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不能停用自己")
    except AdminUserNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    except UserAlreadyInactiveError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户已停用")
    await audit_event(
        request,
        AuditAction.USER_DISABLE,
        user=current_user,
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username},
    )
    tenant_name = user.tenant.name if user.tenant is not None else ""
    return UserAdminOut(
        id=user.id,
        tenant_id=user.tenant_id,
        tenant_name=tenant_name,
        username=user.username,
        email=user.email,
        role=Role(user.role),
        is_active=user.is_active,
    )
