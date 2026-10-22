"""租户设置：本租户成员管理（ADM-04）。

仅对具备 members 读写权限的角色开放；数据始终限定在当前会话租户。
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, col, select

from app.api.deps import audit_event, get_db, require_permission
from app.audit.models import AuditAction
from app.models.membership import Membership
from app.models.user import User
from app.schemas.auth import UserInfo
from app.schemas.tenant import MemberCreate
from app.services.tenant_admin import (
    EmailTakenError,
    TenantInactiveError,
    TenantNotFoundError,
    UsernameTakenError,
    create_member,
)

router = APIRouter(prefix="/tenant", tags=["tenant-settings"])


@router.get("/members", response_model=list[UserInfo])
def list_tenant_members(
    session: Session = Depends(get_db),
    current_user: User = Depends(require_permission("members", "read")),
) -> list[UserInfo]:
    """列出当前租户成员（经 memberships 关联）。"""
    rows = session.exec(
        select(User)
        .join(Membership, col(Membership.user_id) == User.id)
        .where(col(Membership.tenant_id) == current_user.tenant_id)
        .order_by(User.username)
    ).all()
    return [UserInfo.model_validate(row) for row in rows]


@router.post("/members", response_model=UserInfo, status_code=status.HTTP_201_CREATED)
async def create_tenant_member(
    body: MemberCreate,
    request: Request,
    session: Session = Depends(get_db),
    current_user: User = Depends(require_permission("members", "write")),
) -> UserInfo:
    """在当前租户下创建成员。角色固定为 member。"""
    try:
        member = create_member(
            session,
            tenant_id=current_user.tenant_id,
            username=body.username,
            password=body.password,
            email=body.email,
        )
    except TenantNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租户不存在")
    except TenantInactiveError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="租户已停用")
    except UsernameTakenError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    except EmailTakenError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已存在")
    await audit_event(
        request,
        AuditAction.USER_CREATE,
        user=current_user,
        resource_type="user",
        resource_id=member.id,
        details={
            "username": member.username,
            "tenant_id": member.tenant_id,
            "role": member.role,
            "via": "tenant_settings",
        },
    )
    return UserInfo.model_validate(member)
