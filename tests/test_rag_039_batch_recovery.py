"""Observed Regression F03: real import entry, isolated SQL/source, synthetic vectors."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest
from sqlmodel import Session, col, select

from app.models.rag import Document, DocumentChunk, ImportBatch, ImportJob
from app.rag import import_jobs as jobs
from app.rag.embeddings.factory import set_embedding_override
from app.rag.embeddings.mock import MockEmbeddingProvider
from tests import test_rag_039_import_recovery as lifecycle
from tests.test_rag_039_import_recovery import upload, user


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    yield from lifecycle.isolated.__wrapped__(monkeypatch, tmp_path)


def batch_values(db, batch_id):
    with Session(db) as session:
        batch = session.get(ImportBatch, batch_id)
        return (batch.status, batch.total_jobs, batch.completed_jobs,
                batch.successful_jobs, batch.failed_jobs)


def persisted_jobs(db, batch_id):
    with Session(db) as session:
        return [(job.id, job.status, job.attempt_count, job.document_id) for job in session.exec(
            select(ImportJob).where(col(ImportJob.batch_id) == batch_id)
        ).all()]


async def completed_batch(db, statuses=("success", "success")):
    actor = user()
    with Session(db) as session:
        key = jobs.create_import_batch(session, actor, total_jobs=2, source_type="file").id
    keys = [upload(db, actor, filename=f"source{n}.txt", batch=key) for n in range(2)]
    await jobs.run_import_jobs_once(limit=2)
    with Session(db) as session:
        for job_id, status in zip(keys, statuses, strict=True):
            job = session.get(ImportJob, job_id)
            job.status = status
            session.add(job)
        batch = session.get(ImportBatch, key)
        batch.status, batch.completed_jobs, batch.successful_jobs, batch.failed_jobs = "running", 1, 1, 0
        session.add(batch)
        session.commit()
    return actor, key, keys


async def test_final_batch_commit_failure_recovers_without_republishing(isolated, monkeypatch):
    actor = user()
    with Session(isolated) as session:
        batch_id = jobs.create_import_batch(session, actor, total_jobs=2, source_type="file").id
    keys = [upload(isolated, actor, batch=batch_id) for _ in range(2)]
    original_commit = Session.commit
    failed = False
    def fail_final(session):
        nonlocal failed
        if not failed and any(isinstance(row, ImportBatch) and row.completed_jobs == 2 for row in session.dirty):
            failed = True
            raise RuntimeError("synthetic batch final commit failure")
        return original_commit(session)
    with monkeypatch.context() as fault:
        fault.setattr(Session, "commit", fail_final)
        with pytest.raises(RuntimeError, match="synthetic batch final"):
            await jobs.run_import_jobs_once(limit=2)
    assert failed
    before = persisted_jobs(isolated, batch_id)
    assert sorted(row[1] for row in before) == ["success", "success"]
    assert batch_values(isolated, batch_id) == ("running", 2, 1, 1, 0)
    with Session(isolated) as session:
        document_ids = {doc.id for doc in session.exec(select(Document)).all()}
        chunk_ids = {chunk.id for chunk in session.exec(select(DocumentChunk)).all()}
    class NoReembed(MockEmbeddingProvider):
        async def embed(self, texts):
            pytest.fail("terminal child jobs must not be replayed")
    set_embedding_override(NoReembed())
    assert jobs.recover_interrupted_import_jobs() == 0
    assert await jobs.run_import_jobs_until_idle(limit=2) == 0
    assert batch_values(isolated, batch_id) == ("success", 2, 2, 2, 0)
    assert persisted_jobs(isolated, batch_id) == before
    assert {row[0] for row in before} == set(keys)
    with Session(isolated) as session:
        assert {doc.id for doc in session.exec(select(Document)).all()} == document_ids
        assert {chunk.id for chunk in session.exec(select(DocumentChunk)).all()} == chunk_ids
    assert jobs.recover_interrupted_import_jobs() == 0
    assert batch_values(isolated, batch_id) == ("success", 2, 2, 2, 0)


@pytest.mark.parametrize("statuses,expected", [
    (("success", "failed"), ("partial_success", 2, 2, 1, 1)),
    (("failed", "failed"), ("failed", 2, 2, 0, 2)),
    (("pending", "pending"), ("pending", 2, 0, 0, 0)),
    (("success", "pending"), ("running", 2, 1, 1, 0)),
])
async def test_batch_drift_reuses_terminal_and_pending_rules(isolated, statuses, expected):
    _, key, _ = await completed_batch(isolated, statuses)
    before = persisted_jobs(isolated, key)
    assert jobs.recover_interrupted_import_jobs() == 0
    assert batch_values(isolated, key) == expected
    assert persisted_jobs(isolated, key) == before


@pytest.mark.parametrize("binding", ["tenant", "user"])
async def test_cross_binding_does_not_reconcile_foreign_child(isolated, binding):
    _, key, keys = await completed_batch(isolated)
    with Session(isolated) as session:
        child = session.get(ImportJob, keys[-1])
        setattr(child, "tenant_id" if binding == "tenant" else "user_id", "foreign-binding")
        session.add(child)
        session.commit()
    before = persisted_jobs(isolated, key)
    assert jobs.recover_interrupted_import_jobs() == 0
    assert batch_values(isolated, key) == ("running", 2, 1, 1, 0)
    assert persisted_jobs(isolated, key) == before


@pytest.mark.parametrize("count", [0, 1])
def test_empty_or_missing_children_are_not_false_success(isolated, count):
    actor = user()
    with Session(isolated) as session:
        batch = jobs.create_import_batch(session, actor, total_jobs=2, source_type="file")
        key = batch.id
        if count:
            session.add(ImportJob(tenant_id=actor.tenant_id, user_id=actor.id, batch_id=key,
                                  status="success", source_type="file", attempt_count=1))
        batch.status, batch.completed_jobs = "running", count
        session.add(batch)
        session.commit()
    before = batch_values(isolated, key)
    assert jobs.recover_interrupted_import_jobs() == 0
    assert batch_values(isolated, key) == before


async def test_recovery_commit_failure_rolls_back_and_can_retry(isolated, monkeypatch):
    _, key, _ = await completed_batch(isolated)
    original = Session.commit
    def fail_repair(session):
        if any(isinstance(row, ImportBatch) and row.completed_jobs == 2 for row in session.dirty):
            raise RuntimeError("synthetic recovery commit failure")
        return original(session)
    with monkeypatch.context() as fault:
        fault.setattr(Session, "commit", fail_repair)
        with pytest.raises(RuntimeError, match="synthetic recovery"):
            jobs.recover_interrupted_import_jobs()
    assert batch_values(isolated, key) == ("running", 2, 1, 1, 0)
    assert jobs.recover_interrupted_import_jobs() == 0
    assert batch_values(isolated, key) == ("success", 2, 2, 2, 0)


async def test_active_execution_remains_running_during_batch_repair(isolated):
    _, key, keys = await completed_batch(isolated, ("success", "pending"))
    entered, proceed = asyncio.Event(), asyncio.Event()
    class Blocking(MockEmbeddingProvider):
        async def embed(self, texts):
            entered.set()
            await proceed.wait()
            return await super().embed(texts)
    set_embedding_override(Blocking())
    # Distinct content avoids the already persisted deduplication receipt from the fixture.
    with Session(isolated) as session:
        child = session.get(ImportJob, keys[-1])
        from app.rag.document_storage import save_source_file
        child.storage_path = save_source_file(child.tenant_id, b"new active content", "active.txt")
        child.source_name = "active.txt"
        child.document_id = None
        doc = session.exec(select(Document).where(col(Document.import_job_id) == child.id)).first()
        doc.import_job_id = None
        session.add(doc)
        session.add(child)
        session.commit()
    task = asyncio.create_task(jobs.run_import_jobs_once(limit=1))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        before = persisted_jobs(isolated, key)
        assert jobs.recover_interrupted_import_jobs() == 0
        assert batch_values(isolated, key) == ("running", 2, 1, 1, 0)
        assert persisted_jobs(isolated, key) == before
    finally:
        proceed.set()
        await task
    assert batch_values(isolated, key) == ("success", 2, 2, 2, 0)


async def test_fresh_python_process_repairs_terminal_batch_without_replay(isolated):
    _, key, _ = await completed_batch(isolated)
    before = persisted_jobs(isolated, key)
    import app.rag.document_storage as storage
    env = dict(os.environ)
    env.update({"DATABASE_URL": str(isolated.url), "ENV": "development", "EMBEDDING_PROVIDER": "mock",
                "EMBEDDING_DIM": "256", "EMBEDDING_API_KEY": "", "LLM_API_KEY": "",
                "OPENAI_API_KEY": "", "DEEPSEEK_API_KEY": "", "RAG_VECTOR_STORE": "local",
                "RAG_IMPORT_ENABLED": "false", "RAG_CHUNK_STRATEGY": "structured"})
    script = """
import asyncio, sys
from pathlib import Path
from app.rag import document_storage, import_jobs
document_storage._PROJECT_ROOT = Path(sys.argv[1])
assert import_jobs.recover_interrupted_import_jobs() == 0
assert asyncio.run(import_jobs.run_import_jobs_until_idle(limit=2)) == 0
"""
    result = subprocess.run([sys.executable, "-c", script, str(storage._PROJECT_ROOT)], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert batch_values(isolated, key) == ("success", 2, 2, 2, 0)
    assert persisted_jobs(isolated, key) == before


async def test_running_receipt_does_not_shrink_missing_child_batch(isolated):
    _, key, keys = await completed_batch(isolated)
    with Session(isolated) as session:
        session.delete(session.get(ImportJob, keys[-1]))
        child = session.get(ImportJob, keys[0])
        child.status = "running"
        session.add(child)
        batch = session.get(ImportBatch, key)
        batch.total_jobs, batch.completed_jobs, batch.successful_jobs = 2, 0, 0
        session.add(batch)
        session.commit()
    assert jobs.recover_interrupted_import_jobs() == 1
    assert persisted_jobs(isolated, key)[0][1] == "success"
    assert batch_values(isolated, key) == ("running", 2, 0, 0, 0)

@pytest.mark.parametrize("corrupt", [-7, 2**63 - 1])
async def test_corrupt_counter_values_are_rebuilt_from_persisted_children(isolated, corrupt):
    _, key, _ = await completed_batch(isolated)
    with Session(isolated) as session:
        batch = session.get(ImportBatch, key)
        batch.completed_jobs = corrupt
        batch.successful_jobs = corrupt
        batch.failed_jobs = corrupt
        session.add(batch)
        session.commit()
    assert jobs.recover_interrupted_import_jobs() == 0
    assert batch_values(isolated, key) == ("success", 2, 2, 2, 0)


async def test_extreme_declared_total_is_not_silently_reduced(isolated):
    _, key, _ = await completed_batch(isolated)
    with Session(isolated) as session:
        batch = session.get(ImportBatch, key)
        batch.total_jobs = 2**63 - 1
        session.add(batch)
        session.commit()
    before = batch_values(isolated, key)
    assert jobs.recover_interrupted_import_jobs() == 0
    assert batch_values(isolated, key) == before


async def test_batch_reconciliation_is_one_group_query_and_idempotent_no_write(isolated):
    from sqlalchemy import event
    keys = [(await completed_batch(isolated))[1] for _ in range(3)]
    statements = []
    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(isolated, "before_cursor_execute", record)
    try:
        assert jobs.recover_interrupted_import_jobs() == 0
        assert len([sql for sql in statements if "GROUP BY" in sql]) == 1
        assert len([sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]) == 2
        statements.clear()
        assert jobs.recover_interrupted_import_jobs() == 0
        assert len([sql for sql in statements if "GROUP BY" in sql]) == 1
        assert not any(sql.lstrip().upper().startswith("UPDATE") for sql in statements)
    finally:
        event.remove(isolated, "before_cursor_execute", record)
    assert all(batch_values(isolated, key) == ("success", 2, 2, 2, 0) for key in keys)
