"""Add managed agent definitions and versions."""

import sqlalchemy as sa

from alembic import op

revision = "ae41d8362c90"  # pragma: allowlist secret
down_revision = "fd39ab12c4d5"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def _create_table(name: str, *columns: sa.Column) -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(name):
        actual = {column["name"]: column for column in inspector.get_columns(name)}
        primary = inspector.get_pk_constraint(name)["constrained_columns"]
        expected_primary = [column.name for column in columns if column.primary_key]
        foreign = {
            (tuple(key["constrained_columns"]), key["referred_table"], tuple(key["referred_columns"]))
            for key in inspector.get_foreign_keys(name)
        }
        valid = primary == expected_primary
        for column in columns:
            stored = actual.get(column.name)
            if stored is None or bool(stored["nullable"]) != bool(column.nullable):
                valid = False
            for key in column.foreign_keys:
                table, field = key.target_fullname.rsplit(".", 1)
                if ((column.name,), table, (field,)) not in foreign:
                    valid = False
        if not valid:
            raise RuntimeError("Existing agent definition table is incompatible: " + name)
        return
    op.create_table(name, *columns)


def _create_index(name: str, table: str, columns: list[str]) -> None:
    existing = {index["name"]: index for index in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns)
    elif existing[name]["column_names"] != columns:
        raise RuntimeError("Existing agent definition index is incompatible: " + name)


def upgrade() -> None:
    _create_table(
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
    _create_index("ix_agent_definitions_tenant_id", "agent_definitions", ["tenant_id"])
    _create_index("ix_agent_definitions_owner_id", "agent_definitions", ["owner_id"])
    _create_table(
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
