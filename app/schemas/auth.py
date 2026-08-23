"""认证相关请求/响应模型。"""

from pydantic import BaseModel, Field

from app.core.security import Role


class LoginRequest(BaseModel):
    """登录请求（JSON 形式）。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    model_config = {
        "json_schema_extra": {
            "example": {
                "username": "admin",
                "password": "your-password",
            }
        }
    }


class RefreshRequest(BaseModel):
    """刷新令牌请求。"""

    refresh_token: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "refresh_token": "your-refresh-token",
            }
        }
    }


class Token(BaseModel):
    """令牌响应。"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    """创建用户请求（由管理员发起）。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=254)
    # 创建者需具备对应权限；默认成员角色。
    role: Role = Role.MEMBER


class UserInfo(BaseModel):
    """用户信息（对外暴露，不含敏感字段）。"""

    id: str
    tenant_id: str
    username: str
    email: str | None = None
    role: Role
    is_active: bool

    model_config = {"from_attributes": True}
