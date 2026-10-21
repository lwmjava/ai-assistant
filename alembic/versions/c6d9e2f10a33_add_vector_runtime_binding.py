"""add vector runtime and index storage binding"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "c6d9e2f10a33"
down_revision: str | None = "b5c8d3e0f271"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "rag_vector_runtime" not in tables:
        op.create_table(
            "rag_vector_runtime",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("mode", sa.String(), nullable=False),
            sa.Column("uri", sa.String(), nullable=False, server_default=""),
            sa.Column("reason", sa.String(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
    if "rag_index_storage" not in tables:
        op.create_table(
            "rag_index_storage",
            sa.Column("index_id", sa.String(), primary_key=True),
            sa.Column("storage_instance", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "rag_index_storage" in tables:
        op.drop_table("rag_index_storage")
    if "rag_vector_runtime" in tables:
        op.drop_table("rag_vector_runtime")
