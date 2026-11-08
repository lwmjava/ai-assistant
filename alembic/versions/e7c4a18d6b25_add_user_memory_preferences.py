"""add a per-user cross-session memory switch

Revision ID: e7c4a18d6b25
Revises: d1a7c3e5b902
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7c4a18d6b25"  # pragma: allowlist secret
down_revision: str | None = "d1a7c3e5b902"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "user_memory_preferences" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "user_memory_preferences",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_user_memory_preferences_tenant_id", "user_memory_preferences", ["tenant_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "user_memory_preferences" not in set(sa.inspect(bind).get_table_names()):
        return
    count = bind.execute(
        sa.select(sa.func.count()).select_from(sa.table("user_memory_preferences"))
    ).scalar()
    if count:
        raise RuntimeError(
            "user_memory_preferences has data; disable read and write instead of deleting"
        )
    op.drop_index("ix_user_memory_preferences_tenant_id", table_name="user_memory_preferences")
    op.drop_table("user_memory_preferences")
