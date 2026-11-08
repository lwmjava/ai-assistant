"""当前用户是否允许读写自己的跨会话记忆。缺省表示允许。"""

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class UserMemoryPreference(SQLModel, TimestampMixin, table=True):
    """部署开关仍是总闸。这里只能关掉或恢复该用户自己的读写。"""

    __tablename__ = "user_memory_preferences"

    user_id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    enabled: bool = Field(default=True)
