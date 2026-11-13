"""Feedback migrations preserve data and tolerate create_all initialization."""

import pytest
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine

from alembic import command
from alembic.config import Config
from app.core.config import settings


@pytest.mark.parametrize("target", ["head", "d7e0f3a21b44"])  # pragma: allowlist secret
@pytest.mark.parametrize("created", [False, True])
def test_feedback_upgrade_is_idempotent(tmp_path, monkeypatch, created, target):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    engine = create_engine(url)
    if created:
        SQLModel.metadata.create_all(engine)
    cfg = Config("alembic.ini")
    if created:
        command.stamp(cfg, "c6d9e2f10a33")  # pragma: allowlist secret
    command.upgrade(cfg, target)
    command.upgrade(cfg, target)
    assert {"chat_attempts", "chat_feedbacks", "message_ratings"} <= set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO chat_attempts (id,tenant_id,user_id,outcome,created_at,updated_at) "
                "VALUES ('test','t','u','pending',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
    refusal = (
        "data exists"
        if target != "head"
        else "data exists|Authorization confirmations must be retained|Prompt publication history must be retained"
    )
    with pytest.raises(RuntimeError, match=refusal):
        command.downgrade(cfg, "c6d9e2f10a33")  # pragma: allowlist secret
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM chat_attempts")).scalar() == 1
    engine.dispose()
