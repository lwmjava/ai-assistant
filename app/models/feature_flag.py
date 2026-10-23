"""功能开关的持久化覆盖值。

代码侧登记默认值与描述；数据库只保存管理员改过的值，重启后仍生效。
"""

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class FeatureFlag(SQLModel, TimestampMixin, table=True):
    """功能开关覆盖行。"""

    __tablename__ = "feature_flags"

    key: str = Field(primary_key=True, max_length=128)
    value: bool = Field(default=False)
    description: str | None = Field(default=None, max_length=512)
