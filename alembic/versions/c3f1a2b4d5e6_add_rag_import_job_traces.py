"""add_rag_import_job_traces

Revision ID: c3f1a2b4d5e6
Revises: b7d9f3e41d2a
Create Date: 2026-09-06 16:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "c3f1a2b4d5e6"
down_revision: str | None = "b7d9f3e41d2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """升级到此版本。"""
    op.create_table(
        "rag_import_job_traces",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("job_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("tenant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("stage", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("error_code", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("exception_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("message", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("command", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout", sa.Text(), nullable=True),
        sa.Column("stderr", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for name in ["job_id", "tenant_id", "stage", "error_code"]:
        op.create_index(op.f(f"ix_rag_import_job_traces_{name}"), "rag_import_job_traces", [name], unique=False)


def downgrade() -> None:
    """回退到上一版本。"""
    for name in ["error_code", "stage", "tenant_id", "job_id"]:
        op.drop_index(op.f(f"ix_rag_import_job_traces_{name}"), table_name="rag_import_job_traces")
    op.drop_table("rag_import_job_traces")
