"""add governed conversation memory snapshots

Revision ID: f6d2e4a5b7c8
Revises: f5c1d3e4a6b7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6d2e4a5b7c8"
down_revision: str | None = "f5c1d3e4a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "conversation_memory_snapshots" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "conversation_memory_snapshots",
        sa.Column("conversation_id", sa.String(), sa.ForeignKey("conversations.id"), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("user_token_version", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("compressed_count", sa.Integer(), nullable=False),
        sa.Column("prefix_hash", sa.String(length=64), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("compressor_version", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(), nullable=True),
        sa.Column("invalidation_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("tenant_id", "user_id", "expires_at", "invalidated_at"):
        op.create_index(
            f"ix_conversation_memory_snapshots_{column}", "conversation_memory_snapshots", [column]
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "conversation_memory_snapshots" not in set(sa.inspect(bind).get_table_names()):
        return
    count = bind.execute(
        sa.select(sa.func.count()).select_from(sa.table("conversation_memory_snapshots"))
    ).scalar()
    if count:
        raise RuntimeError("conversation_memory_snapshots has data; explicit handling is required")
    op.drop_table("conversation_memory_snapshots")
