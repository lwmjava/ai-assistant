"""Allow workflow datasets in existing evaluation stores without losing rows."""

import re

import sqlalchemy as sa

from alembic import op

revision = "f9b5c7d8e0f1"
down_revision = "f8a4b6c7d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "evaluation_datasets" not in sa.inspect(bind).get_table_names():
        return
    table = sa.Table("evaluation_datasets", sa.MetaData(), autoload_with=bind)
    checks = [constraint for constraint in table.constraints if isinstance(constraint, sa.CheckConstraint)]
    family_checks = [constraint for constraint in checks
                     if re.search(r"\bfamily\b.*\bin\s*\(", str(constraint.sqltext).lower())]
    if family_checks and all("'workflow'" in str(constraint.sqltext).lower() for constraint in family_checks):
        return
    for index, constraint in enumerate(checks):
        if constraint.name is None:
            constraint.name = f"legacy_dataset_check_{index}"
    with op.batch_alter_table("evaluation_datasets", copy_from=table, recreate="always") as batch:
        for constraint in family_checks:
            batch.drop_constraint(constraint.name, type_="check")
        batch.create_check_constraint(
            "ck_eval_dataset_family", "family IN ('retrieval','generation','agent','workflow')",
        )


def downgrade() -> None:
    # Preserve workflow datasets and their linked versions when rolling back code.
    pass
