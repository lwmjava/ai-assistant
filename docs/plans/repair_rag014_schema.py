"""Explicit, backed-up SQLite repair for the stamped RAG cleanup prototype.

Run from the repository root with the project interpreter:
python -m docs.plans.repair_rag014_schema --apply
No documents, vectors, task states or Alembic revision are updated.
"""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

EXPECTED_REVISION = "f9a014c6e001"
LEGACY_COLUMNS = {
    "created_at", "updated_at", "id", "tenant_id", "document_id", "vector_backend",
    "status", "attempt_count", "max_attempts", "next_attempt_at", "last_error_code",
}


def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {
        name: connection.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
        for (name,) in tables
    }


def repair_database(path: Path, backup_dir: Path) -> Path | None:
    """Acquire the writer lock, back up a stable snapshot, then add only the missing column."""
    path = path.resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=30)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchall()
            if revision != [(EXPECTED_REVISION,)]:
                raise RuntimeError("Unexpected revision; refusing targeted repair")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(rag_vector_cleanup_jobs)")}
            if not LEGACY_COLUMNS.issubset(columns):
                raise RuntimeError("Unexpected cleanup schema; refusing targeted repair")
            if "vector_target" in columns:
                connection.rollback()
                return None
            before = _row_counts(connection)
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            backup = backup_dir / f"rag014-before-vector-target-{stamp}-{uuid4().hex[:8]}.db"
            # Separate reader works under BEGIN IMMEDIATE, including SQLite WAL mode.
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as reader:
                with closing(sqlite3.connect(backup)) as destination:
                    reader.backup(destination)
                    if destination.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise RuntimeError("Backup integrity check failed")
                    if _row_counts(destination) != before:
                        raise RuntimeError("Backup row counts changed")
            connection.execute(
                "ALTER TABLE rag_vector_cleanup_jobs ADD COLUMN vector_target VARCHAR NOT NULL DEFAULT ''"
            )
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Repaired database integrity check failed")
            if _row_counts(connection) != before:
                raise RuntimeError("Repair changed row counts")
            connection.commit()
            return backup.resolve()
        except BaseException:
            connection.rollback()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    from sqlalchemy.engine import make_url

    from app.core.config import settings

    url = make_url(settings.DATABASE_URL)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise RuntimeError("Targeted repair supports an existing SQLite file only")
    root = Path(__file__).resolve().parents[2]
    path = Path(url.database).resolve(strict=True)
    if not path.is_relative_to(root / "data"):
        raise RuntimeError("Configured database must be inside the project data directory")
    backup = repair_database(path, root / "data/backups")
    print("repair=applied" if backup else "repair=already_complete")
    if backup:
        print(f"backup={backup}")


if __name__ == "__main__":
    main()
