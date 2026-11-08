"""add cross-session memory metadata

Revision ID: c8e2a91b4d70
Revises: ae41d8362c90
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8e2a91b4d70"  # pragma: allowlist secret
down_revision: str | None = "ae41d8362c90"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "cross_session_memories" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "cross_session_memories",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("user_token_version", sa.Integer(), nullable=False),
        sa.Column("source_conversation_id", sa.String(), nullable=False),
        sa.Column("source_message_id", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding_json", sa.Text(), nullable=False),
        sa.Column("embedding_provider", sa.String(length=64), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in (
        "tenant_id",
        "user_id",
        "source_conversation_id",
        "source_message_id",
        "expires_at",
        "invalidated_at",
    ):
        op.create_index(f"ix_cross_session_memories_{column}", "cross_session_memories", [column])


def downgrade() -> None:
    bind = op.get_bind()
    if "cross_session_memories" not in set(sa.inspect(bind).get_table_names()):
        return
    count = bind.execute(
        sa.select(sa.func.count()).select_from(sa.table("cross_session_memories"))
    ).scalar()
    if count:
        raise RuntimeError("cross_session_memories has data; disable read and write instead of deleting")
    op.drop_table("cross_session_memories")
