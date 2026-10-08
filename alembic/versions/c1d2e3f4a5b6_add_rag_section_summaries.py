"""add rag section summary jobs and summaries

Revision ID: c1d2e3f4a5b6
Revises: b2c3d4e5f607
Create Date: 2026-10-08 12:00:00.000000

RAG-040：版本绑定章节摘要（派生数据，默认关闭，不进向量检索）。
两张表都沿源文档保留，软删/失效时不在这里物理删除。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b2c3d4e5f607"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "rag_section_summary_jobs" not in tables:
        op.create_table(
            "rag_section_summary_jobs",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("document_id", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("source_version_hash", sa.String(length=128), nullable=False),
            sa.Column("chunk_plan_version", sa.String(length=64), nullable=False),
            sa.Column("model", sa.String(length=128), nullable=False),
            sa.Column("prompt_version", sa.String(length=64), nullable=False),
            sa.Column("protocol_version", sa.String(length=64), nullable=False),
            sa.Column("calls_used", sa.Integer(), nullable=False),
            sa.Column("max_calls", sa.Integer(), nullable=False),
            sa.Column("chapters_total", sa.Integer(), nullable=False),
            sa.Column("chapters_ready", sa.Integer(), nullable=False),
            sa.Column("chapters_failed", sa.Integer(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["document_id"], ["rag_documents.id"]),
        )
        op.create_index("ix_rag_section_summary_jobs_tenant_id", "rag_section_summary_jobs", ["tenant_id"])
        op.create_index("ix_rag_section_summary_jobs_document_id", "rag_section_summary_jobs", ["document_id"])
        op.create_index("ix_rag_section_summary_jobs_status", "rag_section_summary_jobs", ["status"])
        op.create_index(
            "ix_rag_section_summary_jobs_source_version_hash", "rag_section_summary_jobs", ["source_version_hash"]
        )

    if "rag_section_summaries" not in tables:
        op.create_table(
            "rag_section_summaries",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("document_id", sa.String(length=64), nullable=False),
            sa.Column("chunk_id", sa.String(length=64), nullable=False),
            sa.Column("source_version_hash", sa.String(length=128), nullable=False),
            sa.Column("chunk_plan_version", sa.String(length=64), nullable=False),
            sa.Column("model", sa.String(length=128), nullable=False),
            sa.Column("prompt_version", sa.String(length=64), nullable=False),
            sa.Column("protocol_version", sa.String(length=64), nullable=False),
            sa.Column("summary_text", sa.Text(), nullable=True),
            sa.Column("source_chunk_ids", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["document_id"], ["rag_documents.id"]),
            sa.ForeignKeyConstraint(["chunk_id"], ["rag_document_chunks.id"]),
        )
        op.create_index("ix_rag_section_summaries_tenant_id", "rag_section_summaries", ["tenant_id"])
        op.create_index("ix_rag_section_summaries_document_id", "rag_section_summaries", ["document_id"])
        op.create_index("ix_rag_section_summaries_chunk_id", "rag_section_summaries", ["chunk_id"])
        op.create_index("ix_rag_section_summaries_status", "rag_section_summaries", ["status"])
        op.create_index(
            "ix_rag_section_summaries_source_version_hash", "rag_section_summaries", ["source_version_hash"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "rag_section_summaries" in tables:
        for name in (
            "ix_rag_section_summaries_source_version_hash",
            "ix_rag_section_summaries_status",
            "ix_rag_section_summaries_chunk_id",
            "ix_rag_section_summaries_document_id",
            "ix_rag_section_summaries_tenant_id",
        ):
            try:
                op.drop_index(name, table_name="rag_section_summaries")
            except Exception:  # noqa: BLE001 — 索引可能缺失
                pass
        op.drop_table("rag_section_summaries")
    if "rag_section_summary_jobs" in tables:
        for name in (
            "ix_rag_section_summary_jobs_source_version_hash",
            "ix_rag_section_summary_jobs_status",
            "ix_rag_section_summary_jobs_document_id",
            "ix_rag_section_summary_jobs_tenant_id",
        ):
            try:
                op.drop_index(name, table_name="rag_section_summary_jobs")
            except Exception:  # noqa: BLE001
                pass
        op.drop_table("rag_section_summary_jobs")
