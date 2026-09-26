"""用户在租户中的成员关系，以及加入租户用的邀请码。"""

import uuid
from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


def _uuid() -> str:
    """生成短 UUID 主键。"""
    return uuid.uuid4().hex


class Membership(SQLModel, TimestampMixin, table=True):
    """用户加入某个租户后的一条关系。角色只表示租户内身份。"""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    tenant_id: str = Field(foreign_key="tenants.id", index=True)
    role: str = Field(default="member")


class Invitation(SQLModel, TimestampMixin, table=True):
    """邀请码。角色固定为 member，管理员可以再次看到邀请码本身。"""

    __tablename__ = "invitations"

    id: str = Field(default_factory=_uuid, primary_key=True)
    code: str = Field(unique=True, index=True)
    tenant_id: str = Field(foreign_key="tenants.id", index=True)
    role: str = Field(default="member")
    created_by: str = Field(foreign_key="users.id", index=True)
    expires_at: datetime
    max_uses: int = Field(default=1)
    use_count: int = Field(default=0)
