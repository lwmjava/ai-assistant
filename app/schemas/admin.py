"""系统管理员的用户、租户修改与系统状态契约。"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.security import Role
from app.schemas.auth import UserInfo


class UserRoleUpdate(BaseModel):
    """只改角色。不接受停用或密码，避免和停用接口混在一次请求里。"""

    role: Role

    model_config = {"extra": "forbid"}


class UserAdminOut(UserInfo):
    """用户列表项。带租户名称，编号仍保留，便于对照邀请页。"""

    tenant_name: str


class UserPage(BaseModel):
    """用户分页。"""

    items: list[UserAdminOut]
    total: int
    page: int
    page_size: int


class TenantUpdate(BaseModel):
    """修改未停用租户的名称。"""

    name: str = Field(min_length=1, max_length=128)

    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("租户名称不能为空")
        return stripped


class ProbeStatus(BaseModel):
    """单项连通结果。只表示探测是否成功。"""

    status: str


class VectorProbeStatus(BaseModel):
    """向量库连通结果，并标明当前用的是本地库还是 Milvus。"""

    status: str
    backend: str


class SystemChecks(BaseModel):
    """管理页展示的依赖探测。"""

    database: ProbeStatus
    vector_store: VectorProbeStatus


class SystemStatusOut(BaseModel):
    """系统管理员看到的运行状态。不包含人数或调用量。"""

    status: str
    app: str
    version: str
    env: str
    started_at: datetime
    checks: SystemChecks
