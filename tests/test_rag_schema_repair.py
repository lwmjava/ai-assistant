"""Observed regression: cleanup table predates vector_target but is stamped at head."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def legacy_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE alembic_version (version_num VARCHAR PRIMARY KEY);
            INSERT INTO alembic_version VALUES ('f9a014c6e001');
            CREATE TABLE rag_documents (id VARCHAR PRIMARY KEY);
            INSERT INTO rag_documents VALUES ('keep-document');
            CREATE TABLE rag_vector_cleanup_jobs (
                created_at DATETIME, updated_at DATETIME, id VARCHAR PRIMARY KEY,
                tenant_id VARCHAR, document_id VARCHAR, vector_backend VARCHAR,
                status VARCHAR, attempt_count INTEGER, max_attempts INTEGER,
                next_attempt_at DATETIME, last_error_code VARCHAR
            );
            INSERT INTO rag_vector_cleanup_jobs
            (id, tenant_id, document_id, vector_backend, status, attempt_count, max_attempts)
            VALUES ('old-job', 'tenant', 'keep-document', 'local', 'pending', 1, 3);
        """)


def test_migration_repairs_existing_table_without_guessing_target(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    migration = load_module("migration", ROOT / "alembic/versions/f9a014c6e001_add_rag_cleaning_and_cleanup_jobs.py")
    engine = sa.create_engine("sqlite:///" + str(path))
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.upgrade()
        row = connection.execute(sa.text(
            "SELECT id, status, attempt_count, vector_target FROM rag_vector_cleanup_jobs"
        )).one()
        assert tuple(row) == ("old-job", "pending", 1, "")
        assert connection.execute(sa.text("SELECT id FROM rag_documents")).scalar() == "keep-document"
    engine.dispose()


@pytest.mark.parametrize("journal_mode", ["delete", "wal"])
def test_explicit_repair_backs_up_and_is_idempotent(tmp_path, journal_mode):
    repair = load_module("repair", ROOT / "docs/plans/repair_rag014_schema.py")
    path = tmp_path / "legacy.db"
    legacy_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA journal_mode={journal_mode}")
    backup = repair.repair_database(path, tmp_path / "backups")
    assert backup is not None and backup.is_file()
    with sqlite3.connect(backup) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(rag_vector_cleanup_jobs)")}
        assert "vector_target" not in columns
        assert connection.execute("SELECT id FROM rag_documents").fetchone() == ("keep-document",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT vector_target, attempt_count FROM rag_vector_cleanup_jobs").fetchone()
        assert row == ("", 1)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("f9a014c6e001",)
    assert repair.repair_database(path, tmp_path / "backups") is None
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1


def test_repair_refuses_unknown_revision_and_missing_file(tmp_path):
    repair = load_module("repair_guard", ROOT / "docs/plans/repair_rag014_schema.py")
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        repair.repair_database(missing, tmp_path / "backups")
    assert not missing.exists()
    path = tmp_path / "legacy.db"
    legacy_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE alembic_version SET version_num = 'unknown'")
    with pytest.raises(RuntimeError, match="revision"):
        repair.repair_database(path, tmp_path / "backups")
    assert not (tmp_path / "backups").exists()


def test_repair_rolls_back_failed_validation_and_keeps_backup(tmp_path, monkeypatch):
    repair = load_module("repair_rollback", ROOT / "docs/plans/repair_rag014_schema.py")
    path = tmp_path / "legacy.db"
    legacy_database(path)
    original_counts = repair._row_counts
    calls = 0

    def fail_after_alter(connection):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("validation failure")
        return original_counts(connection)

    monkeypatch.setattr(repair, "_row_counts", fail_after_alter)
    with pytest.raises(RuntimeError, match="validation failure"):
        repair.repair_database(path, tmp_path / "backups")
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(rag_vector_cleanup_jobs)")}
        assert "vector_target" not in columns
        assert connection.execute("SELECT id FROM rag_documents").fetchone() == ("keep-document",)
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1
