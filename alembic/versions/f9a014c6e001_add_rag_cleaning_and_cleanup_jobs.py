"""Add raw ingestion snapshots and durable retention cleanup jobs."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f9a014c6e001"
down_revision: str | None = "e4b7a2c85d01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from app.models.rag import DocumentIngestionSnapshot, VectorCleanupJob

    bind = op.get_bind()
    DocumentIngestionSnapshot.__table__.create(bind, checkfirst=True)
    VectorCleanupJob.__table__.create(bind, checkfirst=True)
    # create(checkfirst=True) does not upgrade tables made by an earlier prototype.
    columns = {column["name"] for column in sa.inspect(bind).get_columns("rag_vector_cleanup_jobs")}
    if "vector_target" not in columns:
        op.add_column(
            "rag_vector_cleanup_jobs",
            sa.Column("vector_target", sa.String(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for name in ("rag_ingestion_snapshots", "rag_vector_cleanup_jobs"):
        if name in tables and bind.execute(sa.text(f"SELECT COUNT(*) FROM {name}")).scalar():
            raise RuntimeError("Refusing to drop nonempty RAG recovery tables")
    for name in ("rag_ingestion_snapshots", "rag_vector_cleanup_jobs"):
        if name in tables:
            op.drop_table(name)
