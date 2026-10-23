"""add feature_flags table

Revision ID: a3b4c5d6e7f8
Revises: e8f1a2b3c4d5
Create Date: 2026-10-22 23:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: str | None = "e8f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "feature_flags" in tables:
        return
    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "feature_flags" not in tables:
        return
    op.drop_table("feature_flags")
