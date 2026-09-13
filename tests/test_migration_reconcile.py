"""启动迁移对账：修订号落后但表已由 create_all 建出时，应 stamp 而不是重跑 DDL。"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from app.core.config import settings
from app.core.migration import (
    _later_rag_tables_exist,
    _stamp_if_schema_already_at_head,
    auto_migrate,
    get_current_revision,
    get_head_revision,
)


def _import_models() -> None:
    from app.audit import models as _audit_models  # noqa: F401
    from app.models import conversation, rag, user, workflow  # noqa: F401


def _isolated_engine(tmp_path: Path):
    db_path = tmp_path / "reconcile.db"
    url = f"sqlite:///{db_path.as_posix()}"
    return url, create_engine(url, connect_args={"check_same_thread": False})


@pytest.fixture()
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    url, engine = _isolated_engine(tmp_path)
    _import_models()
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    monkeypatch.setattr("app.core.database.engine", engine)
    yield engine
    engine.dispose()


def test_later_rag_tables_exist_after_create_all(isolated_db) -> None:
    assert _later_rag_tables_exist() is True
    tables = set(inspect(isolated_db).get_table_names())
    assert "rag_import_job_traces" in tables


def test_stamp_if_schema_already_at_head_marks_lagging_revision(isolated_db) -> None:
    with isolated_db.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('9a5f2c0e1c3b')"))

    assert get_current_revision() == "9a5f2c0e1c3b"
    assert _stamp_if_schema_already_at_head() is True
    assert get_current_revision() == get_head_revision()


def test_auto_migrate_stamps_instead_of_rerunning_create_table(isolated_db) -> None:
    with isolated_db.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('9a5f2c0e1c3b')"))

    assert auto_migrate() is True
    assert get_current_revision() == get_head_revision()
    tables = set(inspect(isolated_db).get_table_names())
    assert "rag_import_batches" in tables
    assert "rag_import_job_traces" in tables
