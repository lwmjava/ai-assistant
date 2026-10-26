"""add persistent evaluation jobs

Revision ID: f4b0c2d3e5a6
Revises: f3a9b1c2d4e5
Create Date: 2026-10-23 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4b0c2d3e5a6"
down_revision: str | None = "f3a9b1c2d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "evaluation_jobs" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "evaluation_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("submitted_by", sa.String(), nullable=False),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("dataset_version_id", sa.String(), nullable=False),
        sa.Column("runner", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("max_external_calls", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.String(length=160), nullable=True),
        sa.Column("report_path", sa.String(length=500), nullable=True),
        sa.Column("report_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "submitted_by", "idempotency_key", name="uq_eval_job_idempotency"
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','cancel_requested','succeeded','failed','cancelled','interrupted')"
        ),
        sa.CheckConstraint("runner IN ('retrieval','generation','agent','workflow')"),
        sa.CheckConstraint("mode IN ('smoke','synthetic','real')"),
        sa.CheckConstraint("max_external_calls >= 0"),
    )
    for column in (
        "tenant_id", "submitted_by", "dataset_id", "dataset_version_id", "runner", "status"
    ):
        op.create_index(f"ix_evaluation_jobs_{column}", "evaluation_jobs", [column])


def downgrade() -> None:
    bind = op.get_bind()
    if "evaluation_jobs" not in set(sa.inspect(bind).get_table_names()):
        return
    count = bind.execute(sa.select(sa.func.count()).select_from(sa.table("evaluation_jobs"))).scalar()
    if count:
        raise RuntimeError("evaluation_jobs has data; downgrade requires explicit data handling")
    op.drop_table("evaluation_jobs")
