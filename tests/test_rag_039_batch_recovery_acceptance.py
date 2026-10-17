"""Independent F03 acceptance through persisted import and recovery entries."""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import Session, select

from app.models.rag import Document, DocumentChunk, ImportBatch, ImportJob, ImportJobTrace
from app.models.user import User
from app.rag import import_jobs
from tests import test_rag_039_acceptance as acceptance

persisted = acceptance.persisted
upload = acceptance.upload


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Reuse this verifier's own isolation fixture, not implementation helpers."""
    yield from acceptance.isolated.__wrapped__(monkeypatch, tmp_path)


def batch(db, total=2):
    with Session(db) as session:
        actor = session.get(User, "u-a")
        assert actor is not None
        return import_jobs.create_import_batch(session, actor, total_jobs=total, source_type="file").id


def summary(db, batch_id):
    with Session(db) as session:
        row = session.get(ImportBatch, batch_id)
        assert row is not None
        return (row.status, row.total_jobs, row.completed_jobs, row.successful_jobs, row.failed_jobs)


def drift(db, batch_id, *, status="running", total=2, completed=1, successful=1, failed=0):
    with Session(db) as session:
        row = session.get(ImportBatch, batch_id)
        assert row is not None
        row.status, row.total_jobs, row.completed_jobs = status, total, completed
        row.successful_jobs, row.failed_jobs = successful, failed
        session.add(row)
        session.commit()


def snapshot(db):
    with Session(db) as session:
        return {
            model.__name__: sorted(
                (row.model_dump(mode="json") for row in session.exec(select(model)).all()),
                key=lambda row: str(row.get("id", "")),
            )
            for model in (ImportBatch, ImportJob, Document, DocumentChunk, ImportJobTrace)
        }


@pytest.mark.parametrize("reparse", [False, True], ids=["deduplicated", "reparse-receipt"])
async def test_final_batch_commit_fault_recovers_without_replaying_terminal_jobs(isolated, monkeypatch, reparse):
    db, provider = isolated
    batch_id = batch(db)
    if reparse:
        original = upload(db)
        await import_jobs.run_import_jobs_until_idle(limit=1)
        with Session(db) as session:
            actor = session.get(User, "u-a")
            source = session.get(ImportJob, original)
            assert actor is not None and source is not None
            for _ in range(2):
                job = import_jobs.create_reparse_job(session, actor, source.document_id)
                job.batch_id = batch_id
                session.add(job)
                session.commit()
    else:
        upload(db, batch=batch_id)
        upload(db, batch=batch_id)
    real_commit = Session.commit
    triggered = False

    def fail_final_commit(session):
        nonlocal triggered
        if not triggered and any(isinstance(row, ImportBatch) and row.completed_jobs == 2 for row in session.dirty):
            triggered = True
            raise RuntimeError("synthetic final batch commit failure")
        return real_commit(session)

    with monkeypatch.context() as fault:
        fault.setattr(Session, "commit", fail_final_commit)
        with pytest.raises(RuntimeError, match="synthetic final batch"):
            await import_jobs.run_import_jobs_once(limit=2)
    assert triggered
    jobs, docs, chunks = persisted(db)
    assert all(job.status == "success" for job in jobs)
    assert len(docs) == 1
    calls = provider.calls
    identities = ({doc.id for doc in docs}, {chunk.id for chunk in chunks})
    attempts = {job.id: job.attempt_count for job in jobs}
    assert import_jobs.recover_interrupted_import_jobs() == 0
    assert await import_jobs.run_import_jobs_until_idle(limit=2) == 0
    assert summary(db, batch_id) == ("success", 2, 2, 2, 0)
    jobs, docs, chunks = persisted(db)
    assert provider.calls == calls
    assert attempts == {job.id: job.attempt_count for job in jobs}
    assert identities == ({doc.id for doc in docs}, {chunk.id for chunk in chunks})
    stable = snapshot(db)
    assert import_jobs.recover_interrupted_import_jobs() == 0
    assert await import_jobs.run_import_jobs_until_idle(limit=2) == 0
    assert snapshot(db) == stable


@pytest.mark.parametrize("valid", [0, 1], ids=["all-failed", "mixed-partial"])
async def test_terminal_failure_aggregate_repair_does_not_retry_failed_jobs(isolated, valid):
    db, provider = isolated
    batch_id = batch(db)
    ids = [upload(db, source=f"aggregate-{index}.txt", batch=batch_id) for index in range(2)]
    with Session(db) as session:
        for key in ids[valid:]:
            job = session.get(ImportJob, key)
            assert job is not None
            job.storage_path = "data/knowledge/t-a/absent.txt"
            session.add(job)
        session.commit()
    await import_jobs.run_import_jobs_until_idle(limit=2)
    before = persisted(db)
    calls = provider.calls
    drift(db, batch_id, completed=-100, successful=-20, failed=-30)
    assert import_jobs.recover_interrupted_import_jobs() == 0
    state = "failed" if valid == 0 else "partial_success"
    assert summary(db, batch_id) == (state, 2, 2, valid, 2 - valid)
    after = persisted(db)
    assert [(job.id, job.status, job.attempt_count) for job in after[0]] == [
        (job.id, job.status, job.attempt_count) for job in before[0]
    ]
    assert provider.calls == calls


async def test_reconciliation_keeps_pending_and_active_children_nonterminal(isolated):
    db, provider = isolated
    batch_id = batch(db, total=3)
    ids = [upload(db, source=f"pending-{index}.txt", batch=batch_id) for index in range(3)]
    # Equal timestamps are valid; the runner then orders by UUID, not list position.
    with Session(db) as session:
        children = [session.get(ImportJob, key) for key in ids]
        assert all(child is not None for child in children)
        for child in children:
            child.created_at = children[0].created_at
            session.add(child)
        session.commit()
    await import_jobs.run_import_jobs_once(limit=1)
    drift(db, batch_id, status="success", total=3, completed=3, successful=3)
    assert import_jobs.recover_interrupted_import_jobs() == 0
    assert summary(db, batch_id) == ("running", 3, 1, 1, 0)
    provider.hold, provider.started = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(import_jobs.run_import_jobs_once(limit=1))
    await asyncio.wait_for(provider.started.wait(), timeout=2)
    try:
        with Session(db) as session:
            active_children = [child for child in session.exec(select(ImportJob)).all()
                               if child.batch_id == batch_id and child.status == "running"]
            assert len(active_children) == 1
            active = active_children[0]
            assert active.id in ids
            active_id = active.id
            attempt = active.attempt_count
        assert import_jobs.recover_interrupted_import_jobs() == 0
        assert summary(db, batch_id) == ("running", 3, 1, 1, 0)
        with Session(db) as session:
            active = session.get(ImportJob, active_id)
            assert active is not None and active.status == "running" and active.attempt_count == attempt
    finally:
        provider.hold.set()
        await task
    await import_jobs.run_import_jobs_until_idle(limit=1)
    assert summary(db, batch_id) == ("success", 3, 3, 3, 0)


@pytest.mark.parametrize("binding", ["tenant", "user"])
async def test_bad_child_binding_preserves_batch_instead_of_cross_subject_aggregation(isolated, binding):
    db, _ = isolated
    batch_id = batch(db)
    upload(db, source="safe-a.txt", batch=batch_id)
    second = upload(db, source="safe-b.txt", batch=batch_id)
    await import_jobs.run_import_jobs_until_idle(limit=2)
    with Session(db) as session:
        job = session.get(ImportJob, second)
        assert job is not None
        if binding == "tenant":
            job.tenant_id = "t-b"
        else:
            job.user_id = "u-b"
        session.add(job)
        session.commit()
    drift(db, batch_id)
    before = snapshot(db)
    assert import_jobs.recover_interrupted_import_jobs() == 0
    assert snapshot(db) == before
    assert summary(db, batch_id) == ("running", 2, 1, 1, 0)


async def test_missing_child_and_empty_batch_are_not_fabricated_complete(isolated):
    db, provider = isolated
    empty = batch(db)
    incomplete = batch(db)
    key = upload(db, batch=incomplete)
    await import_jobs.run_import_jobs_until_idle(limit=1)
    calls = provider.calls
    with Session(db) as session:
        job = session.get(ImportJob, key)
        assert job is not None
        job.status = "running"
        session.add(job)
        session.commit()
    drift(db, incomplete)
    empty_before = summary(db, empty)
    assert import_jobs.recover_interrupted_import_jobs() == 1
    assert summary(db, empty) == empty_before
    assert summary(db, incomplete) == ("running", 2, 1, 1, 0)
    with Session(db) as session:
        job = session.get(ImportJob, key)
        assert job is not None and job.status == "success" and job.attempt_count == 1
    assert provider.calls == calls


async def test_persistent_reconciliation_commit_failure_raises_and_rolls_back(isolated, monkeypatch):
    db, provider = isolated
    batch_id = batch(db)
    upload(db, batch=batch_id)
    upload(db, batch=batch_id)
    await import_jobs.run_import_jobs_until_idle(limit=2)
    drift(db, batch_id)
    before = snapshot(db)
    calls = provider.calls
    real_commit = Session.commit
    faults = 0

    def database_unavailable(session):
        nonlocal faults
        if any(isinstance(row, ImportBatch) for row in session.dirty):
            faults += 1
            raise RuntimeError("synthetic persistent batch database failure")
        return real_commit(session)

    with monkeypatch.context() as fault:
        fault.setattr(Session, "commit", database_unavailable)
        with pytest.raises(RuntimeError, match="persistent batch"):
            import_jobs.recover_interrupted_import_jobs()
        with pytest.raises(RuntimeError, match="persistent batch"):
            await import_jobs.run_import_jobs_until_idle(limit=2)
    assert faults == 2
    assert snapshot(db) == before
    assert import_jobs.recover_interrupted_import_jobs() == 0
    assert summary(db, batch_id) == ("success", 2, 2, 2, 0)
    assert provider.calls == calls
