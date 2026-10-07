"""add embedding index registry and chunk index_id

Revision ID: b2c3d4e5f607
Revises: a1b2c3d4e5f6
Create Date: 2026-10-07 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "b2c3d4e5f607"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("backend", sa.String(length=32)),
    ("provider", sa.String(length=128)),
    ("model", sa.String(length=128)),
    ("deployment", sa.String(length=128)),
    ("dim", sa.Integer()),
    ("index_version", sa.String(length=32)),
    ("normalization", sa.String(length=32)),
    ("metric", sa.String(length=32)),
    ("identity_key", sa.String(length=512)),
    ("status", sa.String(length=32)),
    ("chunk_count", sa.Integer()),
    ("notes", sa.Text()),
    ("activated_at", sa.DateTime()),
    ("retired_at", sa.DateTime()),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "rag_embedding_indexes" not in tables:
        op.create_table(
            "rag_embedding_indexes",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("name", sa.String(length=256), nullable=False, unique=True),
            *[sa.Column(name, type_, nullable=True) for name, type_ in _INDEX_COLUMNS],
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_rag_embedding_indexes_backend", "rag_embedding_indexes", ["backend"])
        op.create_index("ix_rag_embedding_indexes_model", "rag_embedding_indexes", ["model"])
        op.create_index(
            "ix_rag_embedding_indexes_identity_key", "rag_embedding_indexes", ["identity_key"]
        )
        op.create_index("ix_rag_embedding_indexes_status", "rag_embedding_indexes", ["status"])

    # index_id 必须为可空：历史分块没有索引身份，按 ADR-0008 不得自动认定兼容。
    if "rag_document_chunks" in tables:
        existing = {column["name"] for column in inspector.get_columns("rag_document_chunks")}
        if "index_id" not in existing:
            op.add_column("rag_document_chunks", sa.Column("index_id", sa.String(length=64), nullable=True))
            op.create_index("ix_rag_document_chunks_index_id", "rag_document_chunks", ["index_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "rag_document_chunks" in tables:
        existing = {column["name"] for column in inspector.get_columns("rag_document_chunks")}
        if "index_id" in existing:
            op.drop_index("ix_rag_document_chunks_index_id", table_name="rag_document_chunks")
            with op.batch_alter_table("rag_document_chunks") as batch:
                batch.drop_column("index_id")
    if "rag_embedding_indexes" in tables:
        for name in (
            "ix_rag_embedding_indexes_status",
            "ix_rag_embedding_indexes_identity_key",
            "ix_rag_embedding_indexes_model",
            "ix_rag_embedding_indexes_backend",
        ):
            try:
                op.drop_index(name, table_name="rag_embedding_indexes")
            except Exception:  # noqa: BLE001 — 索引可能由 ORM 建、名字不同
                pass
        op.drop_table("rag_embedding_indexes")
