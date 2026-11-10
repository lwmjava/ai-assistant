"""Add prompt variant compare and publication tables."""

import sqlalchemy as sa

from alembic import op

revision = "fe40bc23d5e6"  # pragma: allowlist secret
down_revision = "a91c0e4b7d63"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def _create_table(name: str, *columns: sa.Column, **kwargs) -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(name):
        return
    op.create_table(name, *columns, **kwargs)


def _create_index(name: str, table: str, columns: list[str]) -> None:
    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns)


def upgrade() -> None:
    _create_table(
        "prompt_variants",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("slot_key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("body_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("slot_key", "label", name="uq_prompt_variant_slot_label"),
        sa.CheckConstraint("status IN ('draft','archived')"),
    )
    _create_index("ix_prompt_variants_slot_key", "prompt_variants", ["slot_key"])
    _create_index("ix_prompt_variants_status", "prompt_variants", ["status"])
    _create_index("ix_prompt_variants_created_by", "prompt_variants", ["created_by"])

    _create_table(
        "prompt_publications",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("slot_key", sa.String(length=64), nullable=False),
        sa.Column("active_variant_id", sa.String(), nullable=False),
        sa.Column("previous_variant_id", sa.String(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("published_by", sa.String(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("slot_key", name="uq_prompt_publication_slot"),
        sa.CheckConstraint("revision >= 0"),
    )
    _create_index("ix_prompt_publications_slot_key", "prompt_publications", ["slot_key"])
    _create_index("ix_prompt_publications_active_variant_id", "prompt_publications", ["active_variant_id"])
    _create_index("ix_prompt_publications_published_by", "prompt_publications", ["published_by"])

    _create_table(
        "prompt_compare_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("slot_key", sa.String(length=64), nullable=False),
        sa.Column("baseline_variant_id", sa.String(), nullable=False),
        sa.Column("candidate_variant_id", sa.String(), nullable=False),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("dataset_version_id", sa.String(), nullable=False),
        sa.Column("dataset_version", sa.String(length=64), nullable=False),
        sa.Column("source_version", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("evaluation_version", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("max_external_calls", sa.Integer(), nullable=False),
        sa.Column("external_calls_used", sa.Integer(), nullable=False),
        sa.Column("binding_json", sa.Text(), nullable=False),
        sa.Column("baseline_metrics_json", sa.Text(), nullable=False),
        sa.Column("candidate_metrics_json", sa.Text(), nullable=False),
        sa.Column("missing_metrics_json", sa.Text(), nullable=False),
        sa.Column("diff_json", sa.Text(), nullable=False),
        sa.Column("cost_json", sa.Text(), nullable=False),
        sa.Column("call_stats_json", sa.Text(), nullable=False),
        sa.Column("auto_publish_blocked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("provider IN ('fake')"),
        sa.CheckConstraint("status IN ('succeeded','failed','incomplete')"),
        sa.CheckConstraint("external_calls_used >= 0"),
        sa.CheckConstraint("max_external_calls >= 0"),
    )
    _create_index("ix_prompt_compare_runs_tenant_id", "prompt_compare_runs", ["tenant_id"])
    _create_index("ix_prompt_compare_runs_created_by", "prompt_compare_runs", ["created_by"])
    _create_index("ix_prompt_compare_runs_slot_key", "prompt_compare_runs", ["slot_key"])
    _create_index("ix_prompt_compare_runs_status", "prompt_compare_runs", ["status"])


def downgrade() -> None:
    raise RuntimeError("Prompt publication history must be retained; disable write paths to roll back")
