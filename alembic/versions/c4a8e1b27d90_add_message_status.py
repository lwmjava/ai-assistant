"""add message status

Revision ID: c4a8e1b27d90
Revises: a9c4e2b81d07
Create Date: 2026-09-26 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "c4a8e1b27d90"
down_revision: str | None = "a9c4e2b81d07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """消息可以标记为正常完成或已停止。已有行视为正常完成。"""
    columns = {col["name"] for col in inspect(op.get_bind()).get_columns("messages")}
    if "status" in columns:
        return
    op.add_column(
        "messages",
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default="complete",
        ),
    )


def downgrade() -> None:
    """去掉消息状态列。"""
    op.drop_column("messages", "status")
