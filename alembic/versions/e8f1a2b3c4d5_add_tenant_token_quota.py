"""add tenant token quota and period columns

Revision ID: e8f1a2b3c4d5
Revises: f2a3b4c5d6e7
Create Date: 2026-10-22 23:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "e8f1a2b3c4d5"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def _add_column(table: str, column: sa.Column) -> None:
    if column.name in _column_names(table):
        return
    op.add_column(table, column)


def _drop_column(table: str, name: str) -> None:
    if name not in _column_names(table):
        return
    op.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {name}"))


def upgrade() -> None:
    _add_column("tenants", sa.Column("token_limit", sa.Integer(), nullable=True))
    _add_column(
        "tenants",
        sa.Column("tokens_used_period", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column("tenants", sa.Column("quota_period_ym", sa.String(), nullable=True))


def downgrade() -> None:
    _drop_column("tenants", "quota_period_ym")
    _drop_column("tenants", "tokens_used_period")
    _drop_column("tenants", "token_limit")
