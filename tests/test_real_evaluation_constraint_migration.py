"""Existing job rows survive widening legacy runner and mode constraints."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_legacy_job_constraints_are_expanded_without_losing_rows(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/f8a4b6c7d9e0_expand_real_evaluation_constraints.py"
    spec = importlib.util.spec_from_file_location("constraint_upgrade", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE evaluation_jobs (id TEXT PRIMARY KEY, runner TEXT NOT NULL, "
            "mode TEXT NOT NULL, CHECK (runner IN ('retrieval','generation','agent')), "
            "CHECK (mode IN ('smoke','synthetic')))"
        )
        connection.exec_driver_sql("INSERT INTO evaluation_jobs VALUES ('old','agent','smoke')")
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        connection.exec_driver_sql("INSERT INTO evaluation_jobs VALUES ('new','workflow','real')")
        assert connection.exec_driver_sql("SELECT id FROM evaluation_jobs ORDER BY id").scalars().all() == [
            "new", "old"
        ]
    engine.dispose()


def test_snapshot_downgrade_drops_only_checks_for_removed_columns(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/f7e3a5b6c8d9_add_real_evaluation_contract.py"
    spec = importlib.util.spec_from_file_location("snapshot_downgrade", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'snapshot.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE evaluation_jobs (\nid TEXT PRIMARY KEY,\nmax_external_calls INTEGER NOT NULL,\n"
            "case_total INTEGER NOT NULL,\ncase_completed INTEGER NOT NULL,\n"
            "CONSTRAINT keep_budget CHECK (max_external_calls >= 0),\n"
            "CONSTRAINT total_check CHECK (case_total >= 0),\nCHECK (case_completed >= 0)\n)"
        )
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        assert {column["name"] for column in sa.inspect(connection).get_columns("evaluation_jobs")} == {
            "id", "max_external_calls",
        }
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql("INSERT INTO evaluation_jobs VALUES ('invalid',-1)")
    engine.dispose()


@pytest.mark.parametrize("named", [False, True])
def test_dataset_family_upgrade_preserves_rows_unique_indexes_and_other_checks(tmp_path, named):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/f9b5c7d8e0f1_expand_dataset_family_constraint.py"
    spec = importlib.util.spec_from_file_location("dataset_family_upgrade", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'dataset.db'}")
    with engine.begin() as connection:
        prefix = "CONSTRAINT old_family " if named else ""
        connection.exec_driver_sql(
            "CREATE TABLE evaluation_datasets (\nid TEXT PRIMARY KEY,\nslug TEXT UNIQUE,\nfamily TEXT NOT NULL,\n"
            + prefix + "CHECK (family IN ('retrieval','generation','agent')),\n"
            "CHECK (length(slug)>0)\n)"
        )
        connection.exec_driver_sql("CREATE INDEX lookup_dataset_family ON evaluation_datasets(family)")
        connection.exec_driver_sql("INSERT INTO evaluation_datasets VALUES ('old','kept','retrieval')")
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.upgrade()
        connection.exec_driver_sql("INSERT INTO evaluation_datasets VALUES ('new','workflow-test','workflow')")
        assert connection.exec_driver_sql("SELECT id FROM evaluation_datasets ORDER BY id").scalars().all() == [
            "new", "old",
        ]
        assert "lookup_dataset_family" in {index["name"] for index in sa.inspect(connection).get_indexes(
            "evaluation_datasets",
        )}
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql("INSERT INTO evaluation_datasets VALUES ('dup','kept','agent')")
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql("INSERT INTO evaluation_datasets VALUES ('empty','','agent')")
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql("INSERT INTO evaluation_datasets VALUES ('bad','invalid','unknown')")
    engine.dispose()
