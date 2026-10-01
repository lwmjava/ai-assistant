"""租户管理请求与响应。"""

from pydantic import BaseModel, Field, field_validator


class TenantCreate(BaseModel):
    """创建租户。名称去掉首尾空白后不能为空。"""

    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("租户名称不能为空")
        return stripped


class MemberCreate(BaseModel):
    """在指定租户下创建成员。不接受角色，避免借此提升权限。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=254)

    model_config = {"extra": "forbid"}

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("用户名不能为空")
        return stripped

    @field_validator("email")
    @classmethod
    def strip_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class TenantOut(BaseModel):
    """对外返回的租户，不含成员。"""

    id: str
    name: str
    is_active: bool

    model_config = {"from_attributes": True}
