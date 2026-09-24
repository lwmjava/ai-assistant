"""one current document per version group

Revision ID: d4e8b1c7a902
Revises: c3f1a2b4d5e6
Create Date: 2026-09-25 01:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e8b1c7a902"
down_revision: str | None = "c3f1a2b4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """同一 version_group_id 只允许一行 is_current。已有重复时失败，不删行。"""
    op.create_index(
        "uq_rag_documents_one_current_version",
        "rag_documents",
        ["version_group_id"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
        postgresql_where=sa.text("is_current"),
    )


def downgrade() -> None:
    """去掉单一当前版约束。"""
    op.drop_index("uq_rag_documents_one_current_version", table_name="rag_documents")
