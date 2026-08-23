"""租户与用户模型（多租户 RBAC）。"""

import uuid
from datetime import UTC, datetime

from sqlmodel import Field, Relationship, SQLModel

from app.core.security import Role
from app.models.base import TimestampMixin


def _uuid() -> str:
    """生成短 UUID 主键（十六进制字符串）。"""
    return uuid.uuid4().hex


class Tenant(SQLModel, TimestampMixin, table=True):
    """租户：多租户隔离的一级边界。"""

    __tablename__ = "tenants"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = Field(index=True)
    is_active: bool = Field(default=True)

    users: list["User"] = Relationship(back_populates="tenant")


class User(SQLModel, TimestampMixin, table=True):
    """用户：归属租户，携带角色与 refresh token 撤销计数。"""

    __tablename__ = "users"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(foreign_key="tenants.id", index=True)
    username: str = Field(unique=True, index=True)
    email: str | None = Field(default=None, unique=True, index=True)
    hashed_password: str
    # 角色以枚举值（字符串）持久化，便于阅读与迁移。
    role: str = Field(default=Role.MEMBER.value)
    # refresh token 撤销计数：每次登出/改密自增，使旧 refresh token 失效。
    token_version: int = Field(default=0)
    is_active: bool = Field(default=True)

    tenant: Tenant | None = Relationship(back_populates="users")

    @property
    def role_enum(self) -> Role:
        """将存储的角色字符串还原为 Role 枚举。"""
        return Role(self.role)
