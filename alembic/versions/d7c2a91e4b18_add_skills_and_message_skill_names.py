"""add skills and message skill names

Revision ID: d7c2a91e4b18
Revises: c4a8e1b27d90
Create Date: 2026-10-01 14:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy import inspect

from alembic import op

revision: str = "d7c2a91e4b18"
down_revision: str | None = "c4a8e1b27d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SKILL_COLUMNS = {
    "id",
    "tenant_id",
    "owner_id",
    "name",
    "scope",
    "name_key",
    "description",
    "constraints",
    "system_prompt",
    "example",
    "keywords",
    "enabled",
    "version",
    "created_at",
    "updated_at",
}


def upgrade() -> None:
    """创建技能表，并给消息加上可空的技能名列。"""
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "skills" in tables:
        present = {col["name"] for col in inspector.get_columns("skills")}
        missing = _SKILL_COLUMNS - present
        if missing:
            raise RuntimeError(f"skills 表已存在但缺少列：{sorted(missing)}")
    else:
        op.create_table(
            "skills",
            sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("tenant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("owner_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("scope", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("name_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("constraints", sa.Text(), nullable=False),
            sa.Column("system_prompt", sa.Text(), nullable=False),
            sa.Column("example", sa.Text(), nullable=False),
            sa.Column("keywords", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name_key"),
        )
        op.create_index(op.f("ix_skills_tenant_id"), "skills", ["tenant_id"], unique=False)
        op.create_index(op.f("ix_skills_owner_id"), "skills", ["owner_id"], unique=False)
        op.create_index(op.f("ix_skills_name"), "skills", ["name"], unique=False)
        op.create_index(op.f("ix_skills_scope"), "skills", ["scope"], unique=False)
        op.create_index(op.f("ix_skills_name_key"), "skills", ["name_key"], unique=False)
        op.create_index(op.f("ix_skills_enabled"), "skills", ["enabled"], unique=False)
        op.create_index("ix_skills_tenant_enabled", "skills", ["tenant_id", "enabled"], unique=False)

    message_columns = {col["name"] for col in inspector.get_columns("messages")}
    if "skill_names" not in message_columns:
        op.add_column("messages", sa.Column("skill_names", sa.Text(), nullable=True))


def downgrade() -> None:
    """先去掉消息上的技能名，再去掉技能表。"""
    bind = op.get_bind()
    inspector = inspect(bind)
    message_columns = {col["name"] for col in inspector.get_columns("messages")}
    if "skill_names" in message_columns:
        with op.batch_alter_table("messages") as batch:
            batch.drop_column("skill_names")
    tables = set(inspector.get_table_names())
    if "skills" in tables:
        op.drop_table("skills")
