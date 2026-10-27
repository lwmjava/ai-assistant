"""Complete persistent runtime tables and nullable columns for migrated stores."""

import sqlalchemy as sa

from alembic import op

revision = "fa06d8e9f1a2"
down_revision = "f9b5c7d8e0f1"
branch_labels = None
depends_on = None


def _timestamps():
    return [sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False)]


def _create(name, columns, indexed=(), constraints=()):
    if name not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(name, *_timestamps(), *columns, *constraints)
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(name)}
    for column in indexed:
        index = f"ix_{name}_{column}"
        if index not in indexes:
            op.create_index(index, name, [column], unique=column == "code")


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        columns = {column["name"]: column for column in sa.inspect(op.get_bind()).get_columns("tenants")}
        for name in ("message_limit", "storage_limit_bytes", "token_limit", "tokens_used_period"):
            if not isinstance(columns[name]["type"], sa.BigInteger):
                op.alter_column("tenants", name, type_=sa.BigInteger(), existing_type=columns[name]["type"])
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("rag_documents")}
    if "uq_rag_documents_one_current_version" not in indexes:
        op.create_index("uq_rag_documents_one_current_version", "rag_documents", ["version_group_id"],
                        unique=True, sqlite_where=sa.text("is_current = 1"), postgresql_where=sa.text("is_current"))
    _create("agent_run_states", [
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("tool_budget_exhausted", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("state_json", sa.String(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    ], ("tenant_id", "user_id", "conversation_id", "message_id", "expires_at"), [
        sa.UniqueConstraint("conversation_id", "message_id", name="ix_agent_run_states_conv_msg"),
    ])
    _create("memberships", [
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
    ], ("user_id", "tenant_id"), [
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),
    ])
    _create("invitations", [
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
    ], ("code", "tenant_id", "created_by"))
    _create("audit_logs", [
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=True),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
    ], ("user_id", "tenant_id", "action", "resource_type"))
    common = ("tenant_id", "workflow_id")
    _create("workflow_owner_alerts", [
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("workflow_name", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("owner_username", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=True),
    ], (*common, "owner_id", "reason"))
    _create("workflow_execution_alerts", [
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("workflow_name", sa.String(), nullable=False),
        sa.Column("execution_id", sa.String(), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("detail", sa.String(), nullable=True),
    ], (*common, "execution_id", "trace_id", "reason"))
    _create("workflow_execution_logs", [
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("execution_id", sa.String(), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
    ], (*common, "execution_id", "trace_id"))
    additions = {
        "messages": [sa.Column("code_results", sa.String(), nullable=True)],
        "rag_documents": [sa.Column("effective_at", sa.DateTime(), nullable=True),
                          sa.Column("expires_at", sa.DateTime(), nullable=True)],
        "rag_document_chunks": [sa.Column("parent_id", sa.String(), nullable=True),
                                sa.Column("strategy", sa.String(), nullable=True),
                                sa.Column("chunk_metadata", sa.String(), nullable=True)],
    }
    for name, columns in additions.items():
        existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(name)}
        for column in columns:
            if column.name not in existing:
                op.add_column(name, column)
    for name, column in (("rag_documents", "effective_at"), ("rag_documents", "expires_at"),
                         ("rag_document_chunks", "parent_id")):
        indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(name)}
        if f"ix_{name}_{column}" not in indexes:
            op.create_index(f"ix_{name}_{column}", name, [column])


def downgrade() -> None:
    # Retain existing runtime records and columns when rolling back application code.
    pass
