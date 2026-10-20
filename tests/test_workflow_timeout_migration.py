"""Workflow timeout schema upgrades preserve existing tasks and execution history."""

import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlmodel import SQLModel

from alembic import command
from alembic.config import Config
from app.core.config import settings
from app.models.workflow import Workflow, WorkflowExecution


def test_timeout_migration_preserves_legacy_workflow(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "a4b7c2d9e160")
    engine = create_engine(url)
    try:
        table = Table("workflows", MetaData(), autoload_with=engine)
        legacy = Workflow(
            id="legacy", tenant_id="tenant", owner_id="owner", name="old task",
            cron_expr="0 * * * *", prompt_template="synthetic task",
        )
        with engine.begin() as connection:
            values = {key: value for key, value in legacy.model_dump().items() if key in table.c}
            connection.execute(table.insert().values(**values))
        def execution_schema():
            return [
                {**column, "type": str(column["type"])}
                for column in inspect(engine).get_columns("workflow_executions")
            ]

        execution_columns = execution_schema()
        for target, present in [("head", True), ("a4b7c2d9e160", False), ("head", True)]:
            if target == "head":
                command.upgrade(config, target)
            else:
                command.downgrade(config, target)
            columns = {column["name"]: column for column in inspect(engine).get_columns("workflows")}
            assert ("timeout_seconds" in columns) is present
            current = Table("workflows", MetaData(), autoload_with=engine)
            with engine.connect() as connection:
                stored = connection.execute(select(current)).mappings().one()
                assert stored["name"] == "old task"
                if present:
                    assert stored["timeout_seconds"] == 300
                    assert columns["timeout_seconds"]["nullable"] is False
            assert execution_schema() == execution_columns
    finally:
        engine.dispose()


def test_timeout_model_and_create_all_schema_upgrade_are_compatible(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    engine = create_engine(url)
    try:
        SQLModel.metadata.create_all(engine)
        row = Workflow(
            tenant_id="tenant", owner_id="owner", name="new task",
            cron_expr="0 * * * *", prompt_template="synthetic task",
        )
        assert getattr(row, "timeout_seconds", None) == 300
        config = Config("alembic.ini")
        command.stamp(config, "a4b7c2d9e160")
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        columns = {column["name"] for column in inspect(engine).get_columns("workflows")}
        assert "timeout_seconds" in columns
    finally:
        engine.dispose()


@pytest.mark.parametrize("recorded_revision", ["a4b7c2d9e160", "9a5f2c0e1c3b"])
def test_startup_migration_adds_timeout_to_previous_schema(tmp_path, monkeypatch, recorded_revision):
    from app.core.migration import auto_migrate

    url = f"sqlite:///{(tmp_path / 'startup.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "a4b7c2d9e160")
    command.stamp(Config("alembic.ini"), recorded_revision)
    engine = create_engine(url)
    monkeypatch.setattr("app.core.database.engine", engine)
    try:
        legacy = Workflow(id="existing-task", tenant_id="tenant", owner_id="owner", name="keep task",
                          cron_expr="* * * * *", prompt_template="synthetic")
        historical = WorkflowExecution(id="existing-history", workflow_id=legacy.id, tenant_id="tenant",
                                       status="success", output="keep historical answer")
        with engine.begin() as connection:
            for table_name, model in [("workflows", legacy), ("workflow_executions", historical)]:
                table = Table(table_name, MetaData(), autoload_with=engine)
                connection.execute(table.insert().values(**{
                    key: value for key, value in model.model_dump().items() if key in table.c}))
        assert auto_migrate() is True
        from sqlmodel import Session
        with Session(engine) as fresh:
            assert fresh.get(Workflow, legacy.id).timeout_seconds == 300
            assert fresh.get(Workflow, legacy.id).name == "keep task"
            assert fresh.get(WorkflowExecution, historical.id).output == "keep historical answer"
        columns = {column["name"] for column in inspect(engine).get_columns("workflows")}
        assert "timeout_seconds" in columns
    finally:
        engine.dispose()


@pytest.mark.parametrize("environment", ["development", "production"])
def test_head_revision_with_missing_timeout_fails_safely(tmp_path, monkeypatch, environment):
    from app.core.migration import auto_migrate, get_current_revision, get_head_revision

    url = f"sqlite:///{(tmp_path / 'drift.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "a4b7c2d9e160")
    command.stamp(Config("alembic.ini"), "head")
    engine = create_engine(url)
    monkeypatch.setattr("app.core.database.engine", engine)
    monkeypatch.setattr(settings, "ENV", environment)
    try:
        if environment == "production":
            with pytest.raises(RuntimeError, match="迁移失败"):
                auto_migrate()
        else:
            assert auto_migrate() is False
        assert get_current_revision() == get_head_revision()
        assert "timeout_seconds" not in {column["name"] for column in inspect(engine).get_columns("workflows")}
    finally:
        engine.dispose()
