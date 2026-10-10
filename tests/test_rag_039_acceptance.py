"""Independent RAG-039 entry acceptance, isolated SQLite and synthetic embeddings.

These cases prove persisted lifecycle behavior, not retrieval quality.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.models.rag import Document, DocumentChunk, ImportBatch, ImportJob
from app.models.user import Tenant, User
from app.rag import import_jobs
from app.rag.document_storage import save_source_file
from app.rag.embeddings.mock import MockEmbeddingProvider


class ObservedEmbedding(MockEmbeddingProvider):
    """A local provider yielding at the real external dependency boundary."""

    def __init__(self) -> None:
        super().__init__()
        self.guard = threading.Lock()
        self.active = 0
        self.peak = 0
        self.calls = 0
        self.hold: asyncio.Event | None = None
        self.started: asyncio.Event | None = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        with self.guard:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls += 1
        try:
            if self.started is not None:
                self.started.set()
            if self.hold is not None:
                await self.hold.wait()
            else:
                await asyncio.sleep(0.03)
            return await super().embed(texts)
        finally:
            with self.guard:
                self.active -= 1


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import app.core.database as database
    import app.rag.document_storage as storage

    db = create_engine(
        f"sqlite:///{(tmp_path / 'case.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 0.05},
    )
    SQLModel.metadata.create_all(db)
    provider = ObservedEmbedding()
    monkeypatch.setattr(import_jobs, "engine", db)
    monkeypatch.setattr(database, "engine", db)
    monkeypatch.setattr(storage, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: provider)
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "local")
    monkeypatch.setattr(settings, "RAG_BACKEND", "native")
    monkeypatch.setattr(settings, "RAG_CHUNK_STRATEGY", "structured")
    monkeypatch.setattr(settings, "RAG_IMPORT_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(settings, "RAG_EFFECTIVE_DATE_FILTER", False)
    with Session(db) as session:
        session.add(Tenant(id="t-a", name="Acceptance A"))
        session.add(Tenant(id="t-b", name="Acceptance B"))
        session.add(User(id="u-a", tenant_id="t-a", username="acceptance-a", hashed_password="", role="tenant_admin"))
        session.add(User(id="u-b", tenant_id="t-b", username="acceptance-b", hashed_password="", role="tenant_admin"))
        session.commit()
    yield db, provider
    db.dispose()


def upload(db, *, source="source.txt", text="original synthetic knowledge", tenant="t-a", batch=None):
    with Session(db) as session:
        user = session.get(User, "u-a" if tenant == "t-a" else "u-b")
        assert user is not None
        path = save_source_file(tenant, text.encode(), source)
        job = import_jobs.create_upload_import_job(
            session, user, storage_path=path, filename=source, content_type="text/plain", batch_id=batch,
        )
        return job.id


def persisted(db):
    with Session(db) as session:
        return (
            list(session.exec(select(ImportJob)).all()),
            list(session.exec(select(Document)).all()),
            list(session.exec(select(DocumentChunk)).all()),
        )


async def test_actual_batch_limit_is_independent_of_concurrent_execution(isolated):
    db, provider = isolated
    for index in range(5):
        upload(db, source=f"batch-{index}.txt", text=f"synthetic batch knowledge {index}")
    assert await import_jobs.run_import_jobs_once(limit=3) == 3
    jobs, docs, _ = persisted(db)
    assert sum(job.status == "success" for job in jobs) == 3
    assert sum(job.status == "pending" for job in jobs) == 2
    assert len(docs) == 3
    assert provider.peak == 2
    assert await import_jobs.run_import_jobs_until_idle(limit=3) == 2


async def test_same_source_contending_runners_publish_one_document(isolated):
    db, _ = isolated
    ids = [upload(db) for _ in range(4)]
    await asyncio.gather(*(import_jobs.run_import_jobs_until_idle(limit=4) for _ in range(3)))
    jobs, docs, chunks = persisted(db)
    assert {job.id for job in jobs} == set(ids)
    assert all(job.status == "success" and job.attempt_count == 1 for job in jobs)
    assert len(docs) == 1
    assert all(job.document_id == docs[0].id for job in jobs)
    assert len(chunks) == docs[0].chunk_count > 0


async def test_changed_same_source_serializes_versions_and_tenants(isolated):
    db, _ = isolated
    first = upload(db, text="version one tenant A")
    second = upload(db, text="version two tenant A")
    foreign = upload(db, text="version one tenant A", tenant="t-b")
    await asyncio.gather(*(import_jobs.run_import_jobs_until_idle(limit=3) for _ in range(2)))
    jobs, docs, _ = persisted(db)
    assert all(job.status == "success" for job in jobs)
    tenant_a = sorted((doc for doc in docs if doc.tenant_id == "t-a"), key=lambda doc: doc.version_number)
    tenant_b = [doc for doc in docs if doc.tenant_id == "t-b"]
    assert len(tenant_a) == 2 and len(tenant_b) == 1
    assert [doc.version_number for doc in tenant_a] == [1, 2]
    assert len({doc.version_group_id for doc in tenant_a}) == 1
    assert sum(doc.is_current for doc in tenant_a) == 1
    assert tenant_b[0].version_group_id != tenant_a[0].version_group_id
    by_id = {job.id: job for job in jobs}
    assert by_id[first].document_id != by_id[second].document_id
    assert by_id[foreign].document_id == tenant_b[0].id


async def test_published_receipt_recovers_terminal_without_reembedding(isolated):
    db, provider = isolated
    job_id = upload(db)
    await import_jobs.run_import_jobs_until_idle(limit=1)
    before_jobs, before_docs, before_chunks = persisted(db)
    calls = provider.calls
    with Session(db) as session:
        job = session.get(ImportJob, job_id)
        assert job is not None
        job.status = "running"
        job.document_id = None
        session.add(job)
        session.commit()
    await import_jobs.run_import_jobs_until_idle(limit=1)
    jobs, docs, chunks = persisted(db)
    assert jobs[0].status == "success"
    assert jobs[0].document_id == before_docs[0].id
    assert jobs[0].attempt_count == before_jobs[0].attempt_count
    assert {doc.id for doc in docs} == {doc.id for doc in before_docs}
    assert {chunk.id for chunk in chunks} == {chunk.id for chunk in before_chunks}
    assert provider.calls == calls


async def test_cancelled_unpublished_job_reenters_and_completes(isolated):
    db, provider = isolated
    job_id = upload(db)
    provider.hold, provider.started = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(import_jobs.run_import_jobs_once(limit=1))
    await asyncio.wait_for(provider.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert persisted(db)[1] == []
    provider.hold = None
    await import_jobs.run_import_jobs_until_idle(limit=1)
    jobs, docs, chunks = persisted(db)
    assert jobs[0].id == job_id and jobs[0].status == "success"
    assert jobs[0].attempt_count == 2
    assert len(docs) == 1 and len(chunks) == docs[0].chunk_count > 0


async def test_scheduler_start_recovers_orphan_using_real_loop(isolated, monkeypatch):
    from app.rag import import_scheduler

    db, _ = isolated
    job_id = upload(db)
    with Session(db) as session:
        job = session.get(ImportJob, job_id)
        assert job is not None
        job.status, job.attempt_count = "running", 1
        session.add(job)
        session.commit()
    monkeypatch.setattr(settings, "RAG_IMPORT_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_IMPORT_INTERVAL_SECONDS", 0.01)
    await import_scheduler.start_scheduler()
    try:
        for _ in range(100):
            if persisted(db)[0][0].status == "success":
                break
            await asyncio.sleep(0.01)
        jobs, docs, _ = persisted(db)
        assert jobs[0].status == "success" and jobs[0].attempt_count == 2
        assert len(docs) == 1
    finally:
        await import_scheduler.stop_scheduler()


async def test_thread_event_loops_share_actual_process_concurrency_cap(isolated):
    db, provider = isolated
    for index in range(6):
        upload(db, source=f"thread-{index}.txt", text=f"thread source {index}")
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(asyncio.run, import_jobs.run_import_jobs_until_idle(limit=6)) for _ in range(3)]
        for future in futures:
            future.result(timeout=5)
    await import_jobs.run_import_jobs_until_idle(limit=6)
    jobs, docs, _ = persisted(db)
    assert all(job.status == "success" and job.attempt_count == 1 for job in jobs)
    assert len(docs) == 6
    assert provider.peak == 2


async def test_quota_tenant_parallel_urls_do_not_fail_database_lock(isolated, monkeypatch):
    db, _ = isolated
    with Session(db) as session:
        tenant = session.get(Tenant, "t-a")
        assert tenant is not None
        tenant.storage_limit_bytes = 100000
        session.add(tenant)
        session.commit()
        user = session.get(User, "u-a")
        assert user is not None
        batch = import_jobs.create_import_batch(session, user, total_jobs=2, source_type="url")
        batch_id = batch.id
        for index in range(2):
            import_jobs.create_url_import_job(
                session, user, url=f"https://example.invalid/quota-{index}.txt", batch_id=batch_id,
            )

    async def local_fetch(url):
        await asyncio.sleep(0)
        return f"quota synthetic document {url}".encode(), "text/plain"

    monkeypatch.setattr(import_jobs, "_fetch_remote_content", local_fetch)
    await import_jobs.run_import_jobs_until_idle(limit=2)
    jobs, docs, chunks = persisted(db)
    assert all(job.status == "success" for job in jobs), [(job.status, job.error) for job in jobs]
    assert len(docs) == 2 and chunks
    with Session(db) as session:
        batch = session.get(ImportBatch, batch_id)
        assert batch is not None
        assert (batch.status, batch.completed_jobs, batch.successful_jobs, batch.failed_jobs) == ("success", 2, 2, 0)


async def test_partial_batch_is_running_until_all_children_finish(isolated):
    db, provider = isolated
    with Session(db) as session:
        user = session.get(User, "u-a")
        assert user is not None
        batch_id = import_jobs.create_import_batch(session, user, total_jobs=3, source_type="file").id
    upload(db, source="done.txt", batch=batch_id)
    missing = upload(db, source="missing.txt", batch=batch_id)
    upload(db, source="last.txt", batch=batch_id)
    with Session(db) as session:
        job = session.get(ImportJob, missing)
        assert job is not None
        job.storage_path = "data/knowledge/t-a/absent.txt"
        session.add(job)
        session.commit()
    await import_jobs.run_import_jobs_once(limit=2)
    with Session(db) as session:
        batch = session.get(ImportBatch, batch_id)
        assert batch is not None
        assert (batch.status, batch.completed_jobs, batch.successful_jobs, batch.failed_jobs) == ("running", 2, 1, 1)
    provider.hold, provider.started = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(import_jobs.run_import_jobs_once(limit=1))
    await asyncio.wait_for(provider.started.wait(), timeout=2)
    try:
        with Session(db) as session:
            batch = session.get(ImportBatch, batch_id)
            assert batch is not None and batch.status == "running"
    finally:
        provider.hold.set()
        await task
    with Session(db) as session:
        batch = session.get(ImportBatch, batch_id)
        assert batch is not None
        summary = (batch.status, batch.completed_jobs, batch.successful_jobs, batch.failed_jobs)
        assert summary == ("partial_success", 3, 2, 1)


async def test_wrong_user_tenant_binding_rejected_before_embedding(isolated):
    db, provider = isolated
    job_id = upload(db)
    with Session(db) as session:
        job = session.get(ImportJob, job_id)
        assert job is not None
        job.user_id = "u-b"
        session.add(job)
        session.commit()
    await import_jobs.run_import_jobs_until_idle(limit=1)
    jobs, docs, chunks = persisted(db)
    assert jobs[0].status == "failed"
    assert provider.calls == 0 and not docs and not chunks


async def test_foreign_reparse_target_never_changes_victim(isolated):
    db, provider = isolated
    upload(db, tenant="t-b")
    await import_jobs.run_import_jobs_until_idle(limit=1)
    _, before_docs, before_chunks = persisted(db)
    with Session(db) as session:
        owner = session.get(User, "u-b")
        assert owner is not None
        job = import_jobs.create_reparse_job(session, owner, before_docs[0].id)
        job_id = job.id
        job.tenant_id, job.user_id = "t-a", "u-a"
        session.add(job)
        session.commit()
    calls = provider.calls
    await import_jobs.run_import_jobs_until_idle(limit=1)
    jobs, docs, chunks = persisted(db)
    assert next(job for job in jobs if job.id == job_id).status == "failed"
    assert provider.calls == calls
    assert {doc.id for doc in docs} == {doc.id for doc in before_docs}
    assert {chunk.id for chunk in chunks} == {chunk.id for chunk in before_chunks}


async def test_failed_new_version_preserves_actual_retrieval_and_retry(isolated, monkeypatch):
    from app.rag.service import RAGService

    db, provider = isolated
    upload(db, text="old knowledge remains discoverable")
    await import_jobs.run_import_jobs_until_idle(limit=1)
    _, before_docs, before_chunks = persisted(db)
    job_id = upload(db, text="replacement knowledge")
    original_embed = provider.embed

    async def unavailable(texts):
        raise OSError("synthetic provider unavailable")

    monkeypatch.setattr(provider, "embed", unavailable)
    await import_jobs.run_import_jobs_until_idle(limit=1)
    jobs, docs, chunks = persisted(db)
    assert next(job for job in jobs if job.id == job_id).status == "failed"
    assert {doc.id for doc in docs} == {doc.id for doc in before_docs}
    assert {chunk.id for chunk in chunks} == {chunk.id for chunk in before_chunks}
    monkeypatch.setattr(provider, "embed", original_embed)
    with Session(db) as session:
        hits = await RAGService(session, "t-a").search("old knowledge remains discoverable")
        assert hits and any(hit.document_id == before_docs[0].id for hit in hits)
        user = session.get(User, "u-a")
        assert user is not None
        import_jobs.retry_import_job(session, user, job_id)
    await import_jobs.run_import_jobs_until_idle(limit=1)
    jobs, docs, _ = persisted(db)
    assert next(job for job in jobs if job.id == job_id).status == "success"
    assert len(docs) == 2 and sum(doc.is_current for doc in docs) == 1


@pytest.mark.parametrize("limit", [0, -1, True, float("nan"), float("inf"), 10**100])
async def test_invalid_scheduler_budget_never_mutates_jobs(isolated, limit):
    db, _ = isolated
    upload(db)
    with pytest.raises(ValueError):
        await import_jobs.run_import_jobs_once(limit=limit)
    jobs, docs, _ = persisted(db)
    assert jobs[0].status == "pending" and jobs[0].attempt_count == 0 and docs == []
