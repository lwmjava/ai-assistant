"""认证相关请求/响应模型。"""

from pydantic import BaseModel, Field, field_validator

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


class RegisterRequest(BaseModel):
    """公开注册。不接受角色，避免把注册变成提权入口。"""

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


class SetupStatus(BaseModel):
    """是否还需要一次性初始化向导。"""

    needs_setup: bool


class RevokeTokensResult(BaseModel):
    """撤销刷新令牌后的新版本号。不含密码。"""

    user_id: str
    token_version: int


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
