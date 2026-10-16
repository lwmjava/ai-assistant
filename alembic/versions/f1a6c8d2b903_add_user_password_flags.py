"""add persisted user test and first password change flags"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "f1a6c8d2b903"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """旧用户默认保持正常账号和已设置密码状态。"""
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("users")}
    for name in ("is_test_user", "must_change_password"):
        if name not in columns:
            op.add_column("users", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    """只移除标记列，账号及密码哈希保持原值。"""
    with op.batch_alter_table("users") as batch:
        batch.drop_column("must_change_password")
        batch.drop_column("is_test_user")
