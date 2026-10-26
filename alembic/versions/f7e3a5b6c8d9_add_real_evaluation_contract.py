"""add real-resource evaluation snapshots and case results

Revision ID: f7e3a5b6c8d9
Revises: f6d2e4a5b7c8
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7e3a5b6c8d9"
down_revision: str | None = "f6d2e4a5b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "evaluation_jobs" in tables:
        columns = {column["name"] for column in sa.inspect(bind).get_columns("evaluation_jobs")}
        additions = (
            ("job_kind", sa.Column("job_kind", sa.String(length=32), nullable=False, server_default="internal_smoke")),
            ("target_type", sa.Column("target_type", sa.String(length=32), nullable=True)),
            ("target_ids_json", sa.Column("target_ids_json", sa.Text(), nullable=False, server_default="[]")),
            ("target_snapshot_json", sa.Column("target_snapshot_json", sa.Text(), nullable=False, server_default="{}")),
            ("adapter_name", sa.Column("adapter_name", sa.String(length=128), nullable=True)),
            ("adapter_version", sa.Column("adapter_version", sa.String(length=64), nullable=True)),
            ("metric_schema_version", sa.Column("metric_schema_version", sa.String(length=64), nullable=True)),
            ("execution_user_id", sa.Column("execution_user_id", sa.String(), nullable=True)),
            ("execution_principal_json", sa.Column("execution_principal_json", sa.Text(), nullable=True)),
            ("options_json", sa.Column("options_json", sa.Text(), nullable=False, server_default="{}")),
            ("budget_json", sa.Column("budget_json", sa.Text(), nullable=False, server_default="{}")),
            ("external_calls_used", sa.Column("external_calls_used", sa.Integer(), nullable=False, server_default="0")),
            ("case_total", sa.Column("case_total", sa.Integer(), nullable=False, server_default="0")),
            ("case_completed", sa.Column("case_completed", sa.Integer(), nullable=False, server_default="0")),
        )
        for name, column in additions:
            if name not in columns:
                op.add_column("evaluation_jobs", column)
        for column in ("job_kind", "target_type", "execution_user_id"):
            index = f"ix_evaluation_jobs_{column}"
            if index not in {item["name"] for item in sa.inspect(bind).get_indexes("evaluation_jobs")}:
                op.create_index(index, "evaluation_jobs", [column])

    if "evaluation_case_results" not in tables:
        op.create_table(
            "evaluation_case_results",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("case_id", sa.String(length=160), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("metrics_json", sa.Text(), nullable=False),
            sa.Column("assertions_json", sa.Text(), nullable=False),
            sa.Column("evidence_json", sa.Text(), nullable=False),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("elapsed_ms", sa.Integer(), nullable=True),
            sa.Column("external_calls_used", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("job_id", "case_id", name="uq_eval_case_result_job_case"),
            sa.CheckConstraint("status IN ('pending','running','passed','failed','error','skipped')"),
            sa.CheckConstraint("external_calls_used >= 0"),
        )
        for column in ("job_id", "tenant_id", "status"):
            op.create_index(f"ix_evaluation_case_results_{column}", "evaluation_case_results", [column])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "evaluation_case_results" in tables:
        count = bind.execute(
            sa.select(sa.func.count()).select_from(sa.table("evaluation_case_results"))
        ).scalar()
        if count:
            raise RuntimeError("evaluation_case_results has data; explicit handling is required")
        op.drop_table("evaluation_case_results")
    if "evaluation_jobs" not in tables:
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns("evaluation_jobs")}
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("evaluation_jobs")}
    removable = (
        "case_completed",
        "case_total",
        "external_calls_used",
        "budget_json",
        "options_json",
        "execution_principal_json",
        "execution_user_id",
        "metric_schema_version",
        "adapter_version",
        "adapter_name",
        "target_snapshot_json",
        "target_ids_json",
        "target_type",
        "job_kind",
    )
    table = sa.Table("evaluation_jobs", sa.MetaData(), autoload_with=bind)
    removable_checks = []
    for index, constraint in enumerate(table.constraints):
        if not isinstance(constraint, sa.CheckConstraint):
            continue
        if any(re.search(rf"\b{re.escape(column)}\b", str(constraint.sqltext)) for column in removable):
            if constraint.name is None:
                constraint.name = f"removed_column_check_{index}"
            removable_checks.append(constraint)
    with op.batch_alter_table("evaluation_jobs", copy_from=table) as batch_op:
        for constraint in removable_checks:
            batch_op.drop_constraint(constraint.name, type_="check")
        for column in ("job_kind", "target_type", "execution_user_id"):
            index = f"ix_evaluation_jobs_{column}"
            if index in indexes:
                batch_op.drop_index(index)
        for column in removable:
            if column in columns:
                batch_op.drop_column(column)
