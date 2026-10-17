"""用户技能与系统全局技能。

内置 YAML 不进这张表。私有技能按创建者隔离；系统全局技能的 tenant_id 为空串。
"""

import uuid

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


def _uuid() -> str:
    """生成短 UUID 主键。"""
    return uuid.uuid4().hex


class Skill(SQLModel, TimestampMixin, table=True):
    """一条可选用的技能声明。"""

    __tablename__ = "skills"
    __table_args__ = (Index("ix_skills_tenant_enabled", "tenant_id", "enabled"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    # 系统全局技能没有租户，存空串。不用 NULL，避免唯一约束在 SQLite 上失效。
    tenant_id: str = Field(default="", index=True)
    owner_id: str = Field(default="", index=True)
    name: str = Field(index=True)
    # private | global
    scope: str = Field(default="private", index=True)
    # private:{tenant}:{owner}:{name} 或 global:{name}
    name_key: str = Field(unique=True, index=True)
    description: str
    constraints: str
    system_prompt: str
    example: str
    # JSON 数组文本。
    keywords: str
    enabled: bool = Field(default=True)
    version: str = Field(default="1.0")
