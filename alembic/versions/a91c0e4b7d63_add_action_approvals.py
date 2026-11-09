"""add persistent action approvals

Revision ID: a91c0e4b7d63
Revises: f3b9d2a18c40
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a91c0e4b7d63"  # pragma: allowlist secret
down_revision: str | None = "f3b9d2a18c40"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "action_approvals" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "action_approvals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("requester_id", sa.String(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resource", sa.String(), nullable=False, server_default="tools"),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False, server_default=""),
        sa.Column("parameter_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("parameter_digest", sa.String(), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False),
        sa.Column("risk", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("execution_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_code", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_action_approvals_tenant_id", "action_approvals", ["tenant_id"])
    op.create_index("ix_action_approvals_requester_id", "action_approvals", ["requester_id"])
    op.create_index("ix_action_approvals_action", "action_approvals", ["action"])
    op.create_index("ix_action_approvals_status", "action_approvals", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    if "action_approvals" not in set(sa.inspect(bind).get_table_names()):
        return
    count = bind.execute(sa.text("SELECT COUNT(*) FROM action_approvals")).scalar()
    if count:
        raise RuntimeError("action_approvals has rows; keep them instead of dropping the table")
    op.drop_index("ix_action_approvals_status", table_name="action_approvals")
    op.drop_index("ix_action_approvals_action", table_name="action_approvals")
    op.drop_index("ix_action_approvals_requester_id", table_name="action_approvals")
    op.drop_index("ix_action_approvals_tenant_id", table_name="action_approvals")
    op.drop_table("action_approvals")
