"""Add evolution reflect, distill, candidate, and skill evaluation tables.

Revision ID: c5d8e1a74b20
Revises: fe40bc23d5e6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5d8e1a74b20"  # pragma: allowlist secret
down_revision: str | None = "fe40bc23d5e6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "evolution_skill_retirements",
    "evolution_skill_evaluations",
    "evolution_candidates",
    "evolution_distill_runs",
    "evolution_reflect_runs",
)


def upgrade() -> None:
    from app.evolution.records import (
        EvolutionCandidate,
        EvolutionDistillRun,
        EvolutionReflectRun,
        EvolutionSkillEvaluation,
        EvolutionSkillRetirement,
    )

    bind = op.get_bind()
    for model in (
        EvolutionReflectRun,
        EvolutionDistillRun,
        EvolutionCandidate,
        EvolutionSkillEvaluation,
        EvolutionSkillRetirement,
    ):
        model.__table__.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    present = [name for name in _TABLES if name in set(sa.inspect(bind).get_table_names())]
    if any(bind.execute(sa.select(sa.func.count()).select_from(sa.table(name))).scalar() for name in present):
        raise RuntimeError("evolution records exist; keep them instead of dropping the tables")
    for name in present:
        op.drop_table(name)
