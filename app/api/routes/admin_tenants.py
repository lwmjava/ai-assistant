"""系统管理员的租户创建与列表。

仅 system_admin 可访问。列出的是未停用租户。
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from app.api.deps import audit_event, get_current_user, get_db
from app.audit.models import AuditAction
from app.core.security import Role
from app.models.user import User
from app.schemas.tenant import TenantCreate, TenantOut
from app.services.tenant_admin import TenantNameTakenError, create_tenant, list_active_tenants

router = APIRouter(prefix="/admin/tenants", tags=["admin-tenants"])


def _require_system_admin(user: User = Depends(get_current_user)) -> User:
    """创建和列出未停用租户只对系统管理员开放。"""
    if user.role_enum is not Role.SYSTEM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅系统管理员可管理租户",
        )
    return user


@router.get("", response_model=list[TenantOut])
def list_tenants(
    session: Session = Depends(get_db),
    _: User = Depends(_require_system_admin),
) -> list[TenantOut]:
    """列出未停用租户。"""
    return [TenantOut.model_validate(row) for row in list_active_tenants(session)]


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant_route(
    body: TenantCreate,
    request: Request,
    session: Session = Depends(get_db),
    current_user: User = Depends(_require_system_admin),
) -> TenantOut:
    """创建租户。未停用租户中名称必须唯一。"""
    try:
        tenant = create_tenant(session, name=body.name)
    except TenantNameTakenError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="租户名称已存在",
        )
    await audit_event(
        request,
        AuditAction.TENANT_CREATE,
        user=current_user,
        resource_type="tenant",
        resource_id=tenant.id,
        details={"name": tenant.name},
    )
    return TenantOut.model_validate(tenant)
