"""add skill draft lifecycle and contract fields

Revision ID: f3b9d2a18c40
Revises: e7c4a18d6b25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3b9d2a18c40"  # pragma: allowlist secret
down_revision: str | None = "e7c4a18d6b25"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEXT = (
    "preconditions",
    "workflow",
    "tools_json",
    "escalation",
    "tests",
    "source_text",
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("skills")}
    if "lifecycle" not in existing:
        op.add_column(
            "skills",
            sa.Column("lifecycle", sa.String(length=16), nullable=False, server_default="active"),
        )
        op.create_index("ix_skills_lifecycle", "skills", ["lifecycle"])
    for name in _TEXT:
        if name in existing:
            continue
        default = "[]" if name == "tools_json" else ""
        op.add_column(
            "skills",
            sa.Column(name, sa.Text(), nullable=False, server_default=default),
        )
    if "supersedes_id" not in existing:
        op.add_column("skills", sa.Column("supersedes_id", sa.String(), nullable=True))
        op.create_index("ix_skills_supersedes_id", "skills", ["supersedes_id"])


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(
        sa.text("SELECT COUNT(*) FROM skills WHERE lifecycle != 'active'")
    ).scalar()
    if count:
        raise RuntimeError("skills has draft or retired rows; disable them instead of dropping columns")
    op.drop_index("ix_skills_supersedes_id", table_name="skills")
    op.drop_column("skills", "supersedes_id")
    for name in reversed(_TEXT):
        op.drop_column("skills", name)
    op.drop_index("ix_skills_lifecycle", table_name="skills")
    op.drop_column("skills", "lifecycle")
