"""add_rag_import_platform

Revision ID: b7d9f3e41d2a
Revises: 9a5f2c0e1c3b
Create Date: 2026-09-05 23:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "b7d9f3e41d2a"
down_revision: Union[str, None] = "9a5f2c0e1c3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级到此版本。"""
    op.create_table(
        "rag_import_batches",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("tenant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("total_jobs", sa.Integer(), nullable=False),
        sa.Column("completed_jobs", sa.Integer(), nullable=False),
        sa.Column("successful_jobs", sa.Integer(), nullable=False),
        sa.Column("failed_jobs", sa.Integer(), nullable=False),
        sa.Column("source_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_rag_import_batches_tenant_id"), "rag_import_batches", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_rag_import_batches_user_id"), "rag_import_batches", ["user_id"], unique=False)
    op.create_index(op.f("ix_rag_import_batches_status"), "rag_import_batches", ["status"], unique=False)

    op.create_table(
        "rag_import_jobs",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("tenant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("batch_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source_uri", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("storage_path", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("content_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("requested_title", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("backend", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("parser_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("document_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("reparse_document_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["rag_import_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for name in [
        "tenant_id",
        "user_id",
        "batch_id",
        "status",
        "source_type",
        "content_hash",
        "document_id",
        "reparse_document_id",
    ]:
        op.create_index(op.f(f"ix_rag_import_jobs_{name}"), "rag_import_jobs", [name], unique=False)

    with op.batch_alter_table("rag_documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="file"))
        batch_op.add_column(sa.Column("source_uri", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("version_group_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("previous_document_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("import_job_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.create_index(batch_op.f("ix_rag_documents_source_kind"), ["source_kind"], unique=False)
        batch_op.create_index(batch_op.f("ix_rag_documents_source_uri"), ["source_uri"], unique=False)
        batch_op.create_index(batch_op.f("ix_rag_documents_content_hash"), ["content_hash"], unique=False)
        batch_op.create_index(batch_op.f("ix_rag_documents_version_group_id"), ["version_group_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_rag_documents_previous_document_id"), ["previous_document_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_rag_documents_is_current"), ["is_current"], unique=False)
        batch_op.create_index(batch_op.f("ix_rag_documents_import_job_id"), ["import_job_id"], unique=False)

    op.execute("UPDATE rag_documents SET version_group_id = id WHERE version_group_id IS NULL")
    with op.batch_alter_table("rag_documents", schema=None) as batch_op:
        batch_op.alter_column("version_group_id", existing_type=sqlmodel.sql.sqltypes.AutoString(), nullable=False)


def downgrade() -> None:
    """回退到上一版本。"""
    with op.batch_alter_table("rag_documents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_rag_documents_import_job_id"))
        batch_op.drop_index(batch_op.f("ix_rag_documents_is_current"))
        batch_op.drop_index(batch_op.f("ix_rag_documents_previous_document_id"))
        batch_op.drop_index(batch_op.f("ix_rag_documents_version_group_id"))
        batch_op.drop_index(batch_op.f("ix_rag_documents_content_hash"))
        batch_op.drop_index(batch_op.f("ix_rag_documents_source_uri"))
        batch_op.drop_index(batch_op.f("ix_rag_documents_source_kind"))
        batch_op.drop_column("import_job_id")
        batch_op.drop_column("is_current")
        batch_op.drop_column("previous_document_id")
        batch_op.drop_column("version_number")
        batch_op.drop_column("version_group_id")
        batch_op.drop_column("content_hash")
        batch_op.drop_column("source_uri")
        batch_op.drop_column("source_kind")

    for name in [
        "reparse_document_id",
        "document_id",
        "content_hash",
        "source_type",
        "status",
        "batch_id",
        "user_id",
        "tenant_id",
    ]:
        op.drop_index(op.f(f"ix_rag_import_jobs_{name}"), table_name="rag_import_jobs")
    op.drop_table("rag_import_jobs")

    op.drop_index(op.f("ix_rag_import_batches_status"), table_name="rag_import_batches")
    op.drop_index(op.f("ix_rag_import_batches_user_id"), table_name="rag_import_batches")
    op.drop_index(op.f("ix_rag_import_batches_tenant_id"), table_name="rag_import_batches")
    op.drop_table("rag_import_batches")
