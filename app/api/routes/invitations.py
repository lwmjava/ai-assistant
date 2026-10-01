"""邀请码：创建、列出和接受。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlmodel import Session

from app.api.deps import audit_event, get_current_user, get_db
from app.audit.models import AuditAction
from app.core.security import Role
from app.models.user import User
from app.schemas.invitation import (
    InvitationAccept,
    InvitationAcceptOut,
    InvitationCreate,
    InvitationOut,
)
from app.services.invitations import (
    AlreadyMemberError,
    InvitationExhaustedError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InviteForbiddenError,
    InviteTenantInactiveError,
    InviteTenantNotFoundError,
    accept_invitation,
    create_invitation,
    list_invitations,
)

router = APIRouter(prefix="/invitations", tags=["invitations"])


def _invite_http(exc: Exception) -> HTTPException:
    """把邀请失败映射成约定的状态码。"""
    if isinstance(exc, InviteForbiddenError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权为该租户发放邀请码")
    if isinstance(exc, InviteTenantNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租户不存在")
    if isinstance(exc, InviteTenantInactiveError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="租户已停用")
    if isinstance(exc, InvitationNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="邀请码不存在")
    if isinstance(exc, InvitationExpiredError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="邀请码已过期")
    if isinstance(exc, InvitationExhaustedError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邀请码已用尽")
    if isinstance(exc, AlreadyMemberError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已在租户中")
    raise exc


@router.post("", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
async def create_invitation_route(
    body: InvitationCreate,
    request: Request,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InvitationOut:
    """发放邀请码。系统管理员必须指定租户。"""
    if current_user.role_enum == Role.SYSTEM_ADMIN and not body.tenant_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请指定租户")
    try:
        row = create_invitation(session, current_user, body.tenant_id)
    except (
        InviteForbiddenError,
        InviteTenantNotFoundError,
        InviteTenantInactiveError,
    ) as exc:
        raise _invite_http(exc) from exc
    await audit_event(
        request,
        AuditAction.OTHER,
        user=current_user,
        resource_type="invitation",
        resource_id=row.id,
        details={"action": "invite_create", "invitation_id": row.id, "tenant_id": row.tenant_id},
    )
    return InvitationOut.model_validate(row)


@router.get("", response_model=list[InvitationOut])
def list_invitation_route(
    tenant_id: str | None = Query(default=None),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[InvitationOut]:
    """列出发码人可见的邀请码。"""
    if current_user.role_enum == Role.SYSTEM_ADMIN and not tenant_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请指定租户")
    try:
        rows = list_invitations(session, current_user, tenant_id)
    except (
        InviteForbiddenError,
        InviteTenantNotFoundError,
        InviteTenantInactiveError,
    ) as exc:
        raise _invite_http(exc) from exc
    return [InvitationOut.model_validate(row) for row in rows]


@router.post("/accept", response_model=InvitationAcceptOut, status_code=status.HTTP_201_CREATED)
async def accept_invitation_route(
    body: InvitationAccept,
    request: Request,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InvitationAcceptOut:
    """凭码加入租户。不切换当前会话租户。"""
    try:
        membership, invitation = accept_invitation(session, current_user, body.code)
    except (
        InvitationNotFoundError,
        InvitationExpiredError,
        InvitationExhaustedError,
        AlreadyMemberError,
        InviteTenantInactiveError,
    ) as exc:
        raise _invite_http(exc) from exc
    await audit_event(
        request,
        AuditAction.OTHER,
        user=current_user,
        resource_type="invitation",
        resource_id=invitation.id,
        details={
            "action": "invite_accept",
            "invitation_id": invitation.id,
            "tenant_id": membership.tenant_id,
        },
    )
    return InvitationAcceptOut(tenant_id=membership.tenant_id, role=membership.role)
