"""add cross-session memory grades, conflicts, and corrections

Revision ID: d1a7c3e5b902
Revises: c8e2a91b4d70
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d1a7c3e5b902"  # pragma: allowlist secret
down_revision: str | None = "c8e2a91b4d70"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("source_grade", sa.String(length=32), False, "user_statement"),
    ("content_hash", sa.String(length=64), False, ""),
    ("subject_key", sa.String(length=64), True, None),
    ("conflict_group_id", sa.String(length=64), True, None),
    ("supersedes_id", sa.String(length=64), True, None),
    ("superseded_at", sa.DateTime(), True, None),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("cross_session_memories")}
    for name, type_, nullable, default in _COLUMNS:
        if name in existing:
            continue
        op.add_column(
            "cross_session_memories",
            sa.Column(name, type_, nullable=nullable, server_default=default),
        )
        op.create_index(
            f"ix_cross_session_memories_{name}", "cross_session_memories", [name]
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "cross_session_memories" not in set(sa.inspect(bind).get_table_names()):
        return
    count = bind.execute(
        sa.select(sa.func.count()).select_from(sa.table("cross_session_memories"))
    ).scalar()
    if count:
        raise RuntimeError(
            "cross_session_memories has data; disable read and write instead of dropping columns"
        )
    for name, _type, _nullable, _default in reversed(_COLUMNS):
        op.drop_index(f"ix_cross_session_memories_{name}", table_name="cross_session_memories")
        op.drop_column("cross_session_memories", name)
