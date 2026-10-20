"""add persisted workflow execution timeout"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "b5c8d3e0f271"
down_revision: str | None = "a4b7c2d9e160"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("workflows")}
    if "timeout_seconds" not in columns:
        op.add_column(
            "workflows",
            sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="300"),
        )


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("workflows")}
    if "timeout_seconds" in columns:
        with op.batch_alter_table("workflows") as batch:
            batch.drop_column("timeout_seconds")
