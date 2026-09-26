"""add message sources

Revision ID: a9c4e2b81d07
Revises: f8b2d4e6c173
Create Date: 2026-09-26 11:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9c4e2b81d07"
down_revision: str | None = "f8b2d4e6c173"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """助手消息可以保存检索来源。已有行保持为空。"""
    op.add_column("messages", sa.Column("sources", sa.String(), nullable=True))


def downgrade() -> None:
    """去掉消息来源列。"""
    op.drop_column("messages", "sources")
