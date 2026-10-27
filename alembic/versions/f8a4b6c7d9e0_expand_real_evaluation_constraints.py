"""Expand existing evaluation job constraints for real resource runs."""

import re

from alembic import op

revision = "f8a4b6c7d9e0"
down_revision = "f7e3a5b6c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import sqlalchemy as sa

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "evaluation_jobs" not in inspector.get_table_names():
        return
    # Give reflected anonymous checks stable names before replacing them.
    table = sa.Table("evaluation_jobs", sa.MetaData(), autoload_with=bind)
    for index, constraint in enumerate(list(table.constraints)):
        if isinstance(constraint, sa.CheckConstraint) and constraint.name is None:
            constraint.name = f"legacy_check_{index}"
    with op.batch_alter_table("evaluation_jobs", copy_from=table, recreate="always") as batch:
        for constraint in table.constraints:
            if isinstance(constraint, sa.CheckConstraint):
                sql = str(constraint.sqltext).lower()
                if any(re.search(
                    rf'^\(*\s*"?{column}"?\s*\)*(?:::\s*(?:text|character varying))?\s*(?:in\s*\(|=\s*any\s*\()',
                    sql,
                ) for column in ("runner", "mode")):
                    batch.drop_constraint(constraint.name, type_="check")
        batch.create_check_constraint("ck_eval_job_runner", "runner IN ('retrieval','generation','agent','workflow')")
        batch.create_check_constraint("ck_eval_job_mode", "mode IN ('smoke','synthetic','real')")


def downgrade() -> None:
    # Widening a permitted value set is retained to preserve real run history.
    pass
