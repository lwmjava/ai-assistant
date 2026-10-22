"""add durable chat attempts and sanitized feedback"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7e0f3a21b44"
down_revision: str | None = "c6d9e2f10a33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    specs = {
        "chat_attempts": [
            sa.Column("conversation_id", sa.String(), nullable=True),
            sa.Column("user_message_id", sa.String(), nullable=True),
            sa.Column("assistant_message_id", sa.String(), nullable=True),
            sa.Column("outcome", sa.String(), nullable=False),
            sa.CheckConstraint("outcome IN ('pending','success','api_error','no_response','stopped')"),
        ],
        "chat_feedbacks": [
            sa.Column("attempt_id", sa.String(), nullable=False),
            sa.Column("conversation_id", sa.String(), nullable=True),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("comment", sa.String(), nullable=True),
            sa.UniqueConstraint("user_id", "attempt_id", name="uq_feedback_owner_attempt"),
            sa.CheckConstraint("kind IN ('api_error','no_response')"),
        ],
        "message_ratings": [
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("assistant_message_id", sa.String(), nullable=False),
            sa.Column("attempt_id", sa.String(), nullable=False),
            sa.Column("rating", sa.String(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=True),
            sa.Column("comment", sa.String(), nullable=True),
            sa.UniqueConstraint("user_id", "assistant_message_id", name="uq_rating_owner_message"),
            sa.CheckConstraint("rating IN ('up','down')"),
        ],
    }
    indexes = {
        "chat_attempts": ["conversation_id", "user_message_id", "assistant_message_id"],
        "chat_feedbacks": ["attempt_id"],
        "message_ratings": ["assistant_message_id"],
    }
    for name, columns in specs.items():
        if name in existing:
            continue
        op.create_table(
            name,
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            *columns,
        )
        for column in ["tenant_id", "user_id", *indexes[name]]:
            op.create_index(f"ix_{name}_{column}", name, [column])


def downgrade() -> None:
    bind = op.get_bind()
    names = [
        n for n in ("message_ratings", "chat_feedbacks", "chat_attempts") if n in sa.inspect(bind).get_table_names()
    ]
    if any(bind.execute(sa.select(sa.func.count()).select_from(sa.table(n))).scalar() for n in names):
        raise RuntimeError("feedback data exists; downgrade requires explicit data handling")
    for name in names:
        op.drop_table(name)
