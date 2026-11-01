"""Store single-use expiring document permission proposals."""

import sqlalchemy as sa

from alembic import op

revision = "fd39ab12c4d5"  # pragma: allowlist secret
down_revision = "fc28fa01b3c4"  # pragma: allowlist secret
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
            raise RuntimeError("Existing authorization table is incompatible: " + name)
        return
    op.create_table(name, *columns)


def _create_index(name: str, table: str, columns: list[str]) -> None:
    existing = {index["name"]: index for index in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns)
    elif existing[name]["column_names"] != columns:
        raise RuntimeError("Existing authorization index is incompatible: " + name)


def upgrade() -> None:
    _create_table(
        "rag_document_acl_proposals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("document_id", sa.String(), sa.ForeignKey("rag_documents.id"), nullable=False),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("proposed_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("proposer_token_version", sa.Integer(), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.String(), nullable=False),
        sa.Column("payload_digest", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_by", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
    )
    _create_index("ix_rag_document_acl_proposals_document_id", "rag_document_acl_proposals", ["document_id"])
    _create_index("ix_rag_document_acl_proposals_tenant_id", "rag_document_acl_proposals", ["tenant_id"])


def downgrade() -> None:
    raise RuntimeError("Authorization confirmations must be retained; disable affected writes to roll back")
