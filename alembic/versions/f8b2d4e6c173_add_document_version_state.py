"""add document version state and operation confirmations

Revision ID: f8b2d4e6c173
Revises: e7c3a9d1b584
Create Date: 2026-09-25 02:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8b2d4e6c173"
down_revision: str | None = "e7c3a9d1b584"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加版本状态，并把已有非当前版标为已替换。"""
    op.add_column(
        "rag_documents",
        sa.Column("version_state", sa.String(), nullable=False, server_default="published"),
    )
    op.create_index("ix_rag_documents_version_state", "rag_documents", ["version_state"])
    op.execute("UPDATE rag_documents SET version_state = 'replaced' WHERE is_current = 0")
    op.create_table(
        "rag_operation_confirmations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rag_operation_confirmations_actor_user_id",
        "rag_operation_confirmations",
        ["actor_user_id"],
    )


def downgrade() -> None:
    """去掉确认表和版本状态。"""
    op.drop_index(
        "ix_rag_operation_confirmations_actor_user_id",
        table_name="rag_operation_confirmations",
    )
    op.drop_table("rag_operation_confirmations")
    op.drop_index("ix_rag_documents_version_state", table_name="rag_documents")
    op.drop_column("rag_documents", "version_state")
