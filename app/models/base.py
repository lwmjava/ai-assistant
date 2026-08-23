"""模型基类与公共字段。"""

from datetime import UTC, datetime

from sqlmodel import SQLModel, Field


def _utcnow() -> datetime:
    """返回当前 UTC 时间（带时区），统一用作时间字段默认值。"""
    return datetime.now(UTC)


class TimestampMixin:
    """时间戳混入：创建时间与更新时间。

    注意：混入类不继承 SQLModel，仅声明字段，由最终的 SQLModel 表类收集，
    以避免与 SQLModel 基类产生 MRO 冲突（pydantic v2）。
    """

    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(
        default_factory=_utcnow,
        nullable=False,
        sa_column_kwargs={"onupdate": _utcnow},
    )
