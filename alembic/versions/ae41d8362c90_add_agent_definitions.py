"""Add managed agent definitions and versions."""

import sqlalchemy as sa

from alembic import op

revision = "ae41d8362c90"  # pragma: allowlist secret
down_revision = "fd39ab12c4d5"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id")),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("active_version", sa.Integer()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_definitions_tenant_id", "agent_definitions", ["tenant_id"])
    op.create_index("ix_agent_definitions_owner_id", "agent_definitions", ["owner_id"])
    op.create_table(
        "agent_definition_versions",
        sa.Column(
            "definition_id",
            sa.String(),
            sa.ForeignKey("agent_definitions.id"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("instructions", sa.String(), nullable=False),
        sa.Column("tool_names_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("agent_definition_versions")
    op.drop_table("agent_definitions")
