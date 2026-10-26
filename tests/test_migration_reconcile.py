"""启动迁移对账：修订号落后但表已由 create_all 建出时，应 stamp 而不是重跑 DDL。"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from app.core.config import settings
from app.core.migration import (
    _ensure_evaluation_job_columns,
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


def test_legacy_reconciliation_stops_at_verified_revision(isolated_db) -> None:
    with isolated_db.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('9a5f2c0e1c3b')"))

    assert get_current_revision() == "9a5f2c0e1c3b"
    assert _stamp_if_schema_already_at_head() is False
    assert get_current_revision() == "a3b4c5d6e7f8"


def test_auto_migrate_stamps_instead_of_rerunning_create_table(isolated_db) -> None:
    with isolated_db.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('9a5f2c0e1c3b')"))

    assert auto_migrate() is True
    assert get_current_revision() == get_head_revision()
    tables = set(inspect(isolated_db).get_table_names())
    assert "rag_import_batches" in tables
    assert "rag_import_job_traces" in tables


def test_head_with_legacy_job_checks_runs_constraint_upgrade(isolated_db):
    from app.core.migration import stamp

    with isolated_db.begin() as conn:
        conn.execute(text("DROP TABLE evaluation_jobs"))
        conn.execute(text(
            "CREATE TABLE evaluation_jobs (id TEXT PRIMARY KEY, runner TEXT, mode TEXT, "
            "CHECK (runner IN ('retrieval','generation','agent')), "
            "CHECK (mode IN ('smoke','synthetic')))"
        ))
        conn.execute(text("INSERT INTO evaluation_jobs VALUES ('kept','agent','smoke')"))
    stamp(get_head_revision())
    assert auto_migrate() is True
    checks = [item["sqltext"] for item in inspect(isolated_db).get_check_constraints("evaluation_jobs")]
    assert any("'workflow'" in check for check in checks)
    assert any("'real'" in check for check in checks)
    with isolated_db.connect() as conn:
        assert conn.execute(text("SELECT id FROM evaluation_jobs")).scalar_one() == "kept"


def test_auto_migrate_adds_evaluation_job_columns_when_revision_is_head(isolated_db) -> None:
    with isolated_db.begin() as conn:
        conn.execute(text("DROP TABLE evaluation_jobs"))
        conn.execute(
            text(
                """
                CREATE TABLE evaluation_jobs (
                    id VARCHAR PRIMARY KEY,
                    tenant_id VARCHAR NOT NULL,
                    submitted_by VARCHAR NOT NULL,
                    dataset_id VARCHAR NOT NULL,
                    dataset_version_id VARCHAR NOT NULL,
                    runner VARCHAR NOT NULL,
                    mode VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    idempotency_key VARCHAR NOT NULL,
                    request_hash VARCHAR NOT NULL,
                    input_hash VARCHAR NOT NULL,
                    max_external_calls INTEGER NOT NULL,
                    report_id VARCHAR,
                    report_path VARCHAR,
                    report_hash VARCHAR,
                    error_code VARCHAR,
                    cancel_requested_at DATETIME,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO evaluation_jobs (
                    id, tenant_id, submitted_by, dataset_id, dataset_version_id,
                    runner, mode, status, idempotency_key, request_hash, input_hash,
                    max_external_calls, created_at, updated_at
                ) VALUES (
                    'job-1', 'tenant-1', 'user-1', 'dataset-1', 'version-1',
                    'retrieval', 'smoke', 'queued', 'key-1', 'req', 'input',
                    0, '2026-10-24 00:00:00', '2026-10-24 00:00:00'
                )
                """
            )
        )
        conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": get_head_revision()},
        )

    assert auto_migrate() is True
    columns = {column["name"] for column in inspect(isolated_db).get_columns("evaluation_jobs")}
    assert "job_kind" in columns
    assert "case_completed" in columns
    with isolated_db.connect() as conn:
        kind = conn.execute(text("SELECT job_kind FROM evaluation_jobs WHERE id = 'job-1'")).scalar_one()
    assert kind == "internal_smoke"

    from sqlmodel import Session

    from app.services.evaluation_jobs import recover_interrupted_jobs

    with Session(isolated_db) as session:
        assert recover_interrupted_jobs(session) == 1
    _ensure_evaluation_job_columns()
    columns_after_repeat = {column["name"] for column in inspect(isolated_db).get_columns("evaluation_jobs")}
    assert columns_after_repeat == columns
