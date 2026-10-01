"""邀请码与成员关系的请求和响应。"""

from datetime import datetime

from pydantic import BaseModel, Field


class InvitationCreate(BaseModel):
    """创建邀请码。不接受角色和次数，避免借此提升权限或放宽使用限制。"""

    tenant_id: str | None = None

    model_config = {"extra": "forbid"}


class InvitationAccept(BaseModel):
    """接受邀请码。"""

    code: str = Field(min_length=1, max_length=128)

    model_config = {"extra": "forbid"}


class InvitationOut(BaseModel):
    """管理员可见的邀请码。"""

    id: str
    code: str
    tenant_id: str
    role: str
    expires_at: datetime
    max_uses: int
    use_count: int

    model_config = {"from_attributes": True}


class InvitationAcceptOut(BaseModel):
    """接受邀请后的成员关系。不含邀请码。"""

    tenant_id: str
    role: str


class MembershipOut(BaseModel):
    """当前用户的一条成员关系。"""

    tenant_id: str
    tenant_name: str
    role: str


class SwitchTenantRequest(BaseModel):
    """切换当前会话租户。只接受请求体。"""

    tenant_id: str = Field(min_length=1)

    model_config = {"extra": "forbid"}
