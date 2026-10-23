"""add conversation soft delete and archive columns

Revision ID: f2a3b4c5d6e7
Revises: d7e0f3a21b44
Create Date: 2026-10-22 23:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "d7e0f3a21b44"
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
    # 先删依赖该列的索引，再 DROP COLUMN（SQLite 3.35+）。
    op.execute(sa.text(f"DROP INDEX IF EXISTS ix_{table}_{name}"))
    op.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {name}"))


def upgrade() -> None:
    _add_column("conversations", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    _add_column("conversations", sa.Column("archived_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    _drop_column("conversations", "archived_at")
    _drop_column("conversations", "deleted_at")
