"""add redacted run history

Revision ID: f5c1d3e4a6b7
Revises: f4b0c2d3e5a6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f5c1d3e4a6b7"
down_revision: str | None = "f4b0c2d3e5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "run_history" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "run_history",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("llm_call_count", sa.Integer(), nullable=False),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("usage_source", sa.String(length=64), nullable=True),
        sa.Column("events_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('succeeded','failed')"),
        sa.CheckConstraint("duration_ms >= 0"),
        sa.CheckConstraint("llm_call_count >= 0"),
        sa.CheckConstraint("tool_call_count >= 0"),
    )
    for column in ("tenant_id", "user_id", "conversation_id", "status"):
        op.create_index(f"ix_run_history_{column}", "run_history", [column])


def downgrade() -> None:
    bind = op.get_bind()
    if "run_history" not in set(sa.inspect(bind).get_table_names()):
        return
    count = bind.execute(sa.select(sa.func.count()).select_from(sa.table("run_history"))).scalar()
    if count:
        raise RuntimeError("run_history has data; downgrade requires explicit data handling")
    op.drop_table("run_history")
