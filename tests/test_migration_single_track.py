"""Startup uses the versioned migration chain and rejects missing revisions."""

import pytest

from app.core import migration
from app.core.config import settings


@pytest.mark.parametrize("production", [False, True])
def test_empty_head_is_a_startup_failure(monkeypatch, production):
    monkeypatch.setattr(settings, "ENV", "production" if production else "development")
    monkeypatch.setattr(migration.ScriptDirectory, "get_current_head", lambda self: None)
    if production:
        with pytest.raises(RuntimeError, match="生产环境数据库迁移失败"):
            migration.auto_migrate()
    else:
        assert migration.auto_migrate() is False


def test_production_startup_never_creates_tables_outside_alembic(monkeypatch):
    from app.core import database

    monkeypatch.setattr(settings, "ENV", "production")
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("Unversioned schema mutation during production startup")

    monkeypatch.setattr(database.SQLModel.metadata, "create_all", forbidden)
    monkeypatch.setattr(migration, "auto_migrate", lambda: calls.append("alembic") or True)
    monkeypatch.setattr(migration, "_ensure_rag_schema_columns", forbidden)
    monkeypatch.setattr(migration, "_ensure_evaluation_job_columns", forbidden)
    database.init_db()
    assert calls == ["alembic"]


def test_default_development_startup_uses_only_alembic(monkeypatch):
    from app.core import database

    monkeypatch.setattr(settings, "ENV", "development")
    calls = []
    monkeypatch.setattr(database.SQLModel.metadata, "create_all",
                        lambda *a, **kw: pytest.fail("unversioned schema write"))
    monkeypatch.setattr(migration, "auto_migrate", lambda: calls.append("alembic") or False)
    database.init_db()
    assert calls == ["alembic"]


def test_disabled_development_migration_preserves_explicit_test_setup(monkeypatch):
    from app.core import database

    monkeypatch.setattr(settings, "ENV", "development")
    calls = []
    monkeypatch.setattr(database.SQLModel.metadata, "create_all", lambda engine: calls.append("create"))
    monkeypatch.setattr(migration, "auto_migrate", lambda: pytest.fail("migration disabled"))
    database.init_db(auto_migrate=False)
    assert calls == ["create"]


@pytest.mark.parametrize("current", [None, "old", "head"])
def test_disabled_production_migration_only_checks_revision(monkeypatch, current):
    from app.core import database

    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(database.SQLModel.metadata, "create_all",
                        lambda *a, **kw: pytest.fail("production unversioned schema write"))
    monkeypatch.setattr(migration, "auto_migrate", lambda: pytest.fail("migration disabled"))
    monkeypatch.setattr(migration, "get_head_revision", lambda: "head")
    monkeypatch.setattr(migration, "get_current_revision", lambda: current)
    if current == "head":
        database.init_db(auto_migrate=False)
    else:
        with pytest.raises(RuntimeError):
            database.init_db(auto_migrate=False)


@pytest.mark.parametrize("production", [False, True])
def test_pending_lookup_errors_follow_environment_failure_policy(monkeypatch, production):
    monkeypatch.setattr(settings, "ENV", "production" if production else "development")
    monkeypatch.setattr(migration, "get_head_revision", lambda: "expected")
    monkeypatch.setattr(migration, "_stamp_legacy_unversioned_schema", lambda: None)
    monkeypatch.setattr(migration, "_stamp_if_schema_already_at_head", lambda: False)

    def fail():
        raise RuntimeError("invalid revision")

    monkeypatch.setattr(migration, "get_pending_migrations", fail)
    if production:
        with pytest.raises(RuntimeError, match="生产环境数据库迁移失败"):
            migration.auto_migrate()
    else:
        assert migration.auto_migrate() is False


def test_upgrade_without_advancing_revision_is_not_success(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(migration, "get_head_revision", lambda: "expected")
    monkeypatch.setattr(migration, "get_pending_migrations", lambda: ["expected"])
    monkeypatch.setattr(migration, "get_current_revision", lambda: "previous")
    monkeypatch.setattr(migration, "upgrade", lambda target: None)
    with pytest.raises(RuntimeError, match="生产环境数据库迁移失败"):
        migration.auto_migrate()


@pytest.fixture()
def migration_database(tmp_path, monkeypatch):
    from sqlalchemy import create_engine

    url = "sqlite:///" + (tmp_path / "single-track.db").as_posix()
    engine = create_engine(url)
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr("app.core.database.engine", engine)
    yield engine
    engine.dispose()


def test_empty_store_uses_complete_chain_and_repeat_is_idempotent(migration_database):
    from sqlalchemy import inspect

    from app.core.database import init_db

    init_db()
    assert migration.get_current_revision() == migration.get_head_revision()
    assert "agent_run_states" in inspect(migration_database).get_table_names()
    init_db()


def test_disabled_production_startup_rejects_schema_drift_without_repair(migration_database):
    from sqlalchemy import inspect, text

    from app.core.database import init_db

    init_db()
    revision = migration.get_current_revision()
    with migration_database.begin() as connection:
        connection.execute(text("ALTER TABLE workflows DROP COLUMN timeout_seconds"))
    with pytest.raises(RuntimeError, match="实际结构不一致"):
        init_db(auto_migrate=False)
    assert migration.get_current_revision() == revision
    assert "timeout_seconds" not in {
        column["name"] for column in inspect(migration_database).get_columns("workflows")
    }


def test_forward_revision_repairs_old_stamped_store_without_losing_messages(migration_database):
    from sqlalchemy import text
    from sqlmodel import Session

    from app.models.conversation import Message

    migration.upgrade("fa06d8e9f1a2")
    with Session(migration_database) as session:
        session.add(Message(id="kept", conversation_id="legacy", role="user", content="保留消息"))
        session.commit()
    with migration_database.begin() as connection:
        connection.execute(text("ALTER TABLE messages DROP COLUMN sources"))
        connection.execute(text("ALTER TABLE messages DROP COLUMN skill_names"))
    assert migration.auto_migrate() is True
    assert migration.auto_migrate() is True
    with Session(migration_database) as session:
        row = session.get(Message, "kept")
        assert row.content == "保留消息"
        assert row.sources is None
        assert row.skill_names is None
