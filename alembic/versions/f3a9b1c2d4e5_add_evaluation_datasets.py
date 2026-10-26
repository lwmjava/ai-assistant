"""add evaluation datasets, versions, and feedback candidates

Revision ID: f3a9b1c2d4e5
Revises: e8f1a2b3c4d5
Create Date: 2026-10-23 16:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a9b1c2d4e5"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "evaluation_datasets" not in existing:
        op.create_table(
            "evaluation_datasets",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("slug", sa.String(length=120), nullable=False),
            sa.Column("family", sa.String(length=32), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("description", sa.String(length=1000), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("tenant_id", "slug", name="uq_eval_dataset_tenant_slug"),
        sa.CheckConstraint("family IN ('retrieval','generation','agent','workflow')"),
        )
        op.create_index("ix_evaluation_datasets_tenant_id", "evaluation_datasets", ["tenant_id"])
        op.create_index("ix_evaluation_datasets_slug", "evaluation_datasets", ["slug"])
        op.create_index("ix_evaluation_datasets_created_by", "evaluation_datasets", ["created_by"])

    if "evaluation_dataset_versions" not in existing:
        op.create_table(
            "evaluation_dataset_versions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("dataset_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("version", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("schema_name", sa.String(length=64), nullable=False),
            sa.Column("cases_json", sa.Text(), nullable=False),
            sa.Column("index_json", sa.Text(), nullable=True),
            sa.Column("case_count", sa.Integer(), nullable=False),
            sa.Column("is_frozen", sa.Boolean(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("reviewed_by", sa.String(), nullable=True),
            sa.Column("review_decision", sa.String(length=64), nullable=True),
            sa.Column("review_notes", sa.String(length=2000), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("dataset_id", "version", name="uq_eval_dataset_version"),
            sa.CheckConstraint("status IN ('draft','pending_review','approved','rejected')"),
            sa.CheckConstraint("source IN ('upload','feedback','ai_generated')"),
        )
        op.create_index("ix_evaluation_dataset_versions_dataset_id", "evaluation_dataset_versions", ["dataset_id"])
        op.create_index("ix_evaluation_dataset_versions_tenant_id", "evaluation_dataset_versions", ["tenant_id"])
        op.create_index("ix_evaluation_dataset_versions_status", "evaluation_dataset_versions", ["status"])
        op.create_index("ix_evaluation_dataset_versions_created_by", "evaluation_dataset_versions", ["created_by"])

    if "evaluation_feedback_candidates" not in existing:
        op.create_table(
            "evaluation_feedback_candidates",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("source_type", sa.String(length=16), nullable=False),
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("attempt_id", sa.String(), nullable=True),
            sa.Column("conversation_id", sa.String(), nullable=True),
            sa.Column("snapshot_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("nominated_by", sa.String(), nullable=False),
            sa.Column("source_user_id", sa.String(), nullable=False),
            sa.Column("reviewed_by", sa.String(), nullable=True),
            sa.Column("review_notes", sa.String(length=2000), nullable=True),
            sa.Column("target_dataset_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "source_type", "source_id", name="uq_eval_candidate_source"
            ),
            sa.CheckConstraint("source_type IN ('feedback','rating')"),
            sa.CheckConstraint("status IN ('pending','accepted','rejected','withdrawn')"),
        )
        op.create_index(
            "ix_evaluation_feedback_candidates_tenant_id",
            "evaluation_feedback_candidates",
            ["tenant_id"],
        )
        op.create_index(
            "ix_evaluation_feedback_candidates_source_id",
            "evaluation_feedback_candidates",
            ["source_id"],
        )
        op.create_index(
            "ix_evaluation_feedback_candidates_attempt_id",
            "evaluation_feedback_candidates",
            ["attempt_id"],
        )
        op.create_index(
            "ix_evaluation_feedback_candidates_status",
            "evaluation_feedback_candidates",
            ["status"],
        )
        op.create_index(
            "ix_evaluation_feedback_candidates_nominated_by",
            "evaluation_feedback_candidates",
            ["nominated_by"],
        )
        op.create_index(
            "ix_evaluation_feedback_candidates_source_user_id",
            "evaluation_feedback_candidates",
            ["source_user_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    names = [
        n
        for n in (
            "evaluation_feedback_candidates",
            "evaluation_dataset_versions",
            "evaluation_datasets",
        )
        if n in sa.inspect(bind).get_table_names()
    ]
    for name in names:
        count = bind.execute(sa.select(sa.func.count()).select_from(sa.table(name))).scalar()
        if count:
            raise RuntimeError(f"{name} has data; downgrade requires explicit data handling")
    for name in names:
        op.drop_table(name)
