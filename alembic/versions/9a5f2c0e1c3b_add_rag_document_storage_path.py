"""add_rag_document_storage_path

Revision ID: 9a5f2c0e1c3b
Revises: 6f8d055bdc4a
Create Date: 2026-09-05 22:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# 修订标识符（Alembic 用于版本追踪）
revision: str = "9a5f2c0e1c3b"
down_revision: Union[str, None] = "6f8d055bdc4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级到此版本。"""
    with op.batch_alter_table("rag_documents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("storage_path", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )


def downgrade() -> None:
    """回退到上一版本。"""
    with op.batch_alter_table("rag_documents", schema=None) as batch_op:
        batch_op.drop_column("storage_path")
