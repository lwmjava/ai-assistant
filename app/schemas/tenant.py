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


class TenantOut(BaseModel):
    """对外返回的租户，不含成员。"""

    id: str
    name: str
    is_active: bool

    model_config = {"from_attributes": True}
