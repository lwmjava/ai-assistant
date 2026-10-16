"""add nullable validated attachments to user messages"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "a4b7c2d9e160"
down_revision: str | None = "f1a6c8d2b903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("messages")}
    if "attachments" not in columns:
        op.add_column("messages", sa.Column("attachments", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("attachments")
