"""add document deleted_at

Revision ID: e7c3a9d1b584
Revises: d4e8b1c7a902
Create Date: 2026-09-25 01:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7c3a9d1b584"
down_revision: str | None = "d4e8b1c7a902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加可空删除时间。已有行保持为空。"""
    op.add_column("rag_documents", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_rag_documents_deleted_at", "rag_documents", ["deleted_at"])


def downgrade() -> None:
    """去掉删除时间列。"""
    op.drop_index("ix_rag_documents_deleted_at", table_name="rag_documents")
    op.drop_column("rag_documents", "deleted_at")
