"""add tenant quota columns

Revision ID: e4b7a2c85d01
Revises: d7c2a91e4b18
Create Date: 2026-10-01 20:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "e4b7a2c85d01"
down_revision: str | None = "d7c2a91e4b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def _add_nullable_integer(table: str, name: str) -> None:
    if name in _column_names(table):
        return
    op.add_column(table, sa.Column(name, sa.Integer(), nullable=True))


def _drop_column(table: str, name: str) -> None:
    if name not in _column_names(table):
        return
    with op.batch_alter_table(table) as batch:
        batch.drop_column(name)


def upgrade() -> None:
    """给租户加上可空的消息和源文件上限，给文档加上可空的源文件字节数。"""
    _add_nullable_integer("tenants", "message_limit")
    _add_nullable_integer("tenants", "storage_limit_bytes")
    _add_nullable_integer("rag_documents", "source_bytes")


def downgrade() -> None:
    """去掉这三列。其他列上的已有对话和文档保留。"""
    _drop_column("rag_documents", "source_bytes")
    _drop_column("tenants", "storage_limit_bytes")
    _drop_column("tenants", "message_limit")
