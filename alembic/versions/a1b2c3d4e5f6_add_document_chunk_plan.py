"""add document chunk_plan

Revision ID: a1b2c3d4e5f6
Revises: f9a014c6e001
Create Date: 2026-10-06 13:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f9a014c6e001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns("rag_documents")}
    if "chunk_plan" not in existing:
        op.add_column("rag_documents", sa.Column("chunk_plan", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns("rag_documents")}
    if "chunk_plan" in existing:
        with op.batch_alter_table("rag_documents") as batch:
            batch.drop_column("chunk_plan")
