"""Independent RAG-039 adversarial entry checks; isolated SQLite, synthetic embedding."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.models.rag import Document, ImportJob
from app.models.user import Tenant, User
from app.rag import import_jobs as jobs
from app.rag.document_storage import save_source_file
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.service import RAGService


@pytest.fixture()
def isolated_review(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import app.rag.document_storage as storage

    bound = create_engine(f"sqlite:///{(tmp_path / 'review.db').as_posix()}")
    SQLModel.metadata.create_all(bound)
    monkeypatch.setattr(jobs, "engine", bound)
    monkeypatch.setattr(storage, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "RAG_BACKEND", "native")
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "local")
    monkeypatch.setattr(settings, "RAG_IMPORT_ENABLED", False)
    monkeypatch.setattr(settings, "RAG_IMPORT_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(settings, "RAG_CHUNK_STRATEGY", "structured")
    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider())
    tenant = Tenant(id=uuid4().hex, name="independent-review")
    user = User(
        id=uuid4().hex, tenant_id=tenant.id, username=uuid4().hex,
        hashed_password="", role="tenant_admin", is_active=True,
    )
    tenant_id, user_id = tenant.id, user.id
    with Session(bound) as session:
        session.add(tenant)
        session.add(user)
        session.commit()
    yield bound, tenant_id, user_id
    bound.dispose()


async def test_runner_rejects_cross_tenant_source_before_embedding(
    isolated_review, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound, tenant_id, user_id = isolated_review
    victim = uuid4().hex
    path = save_source_file(victim, b"victim restricted support policy", "secret.txt")
    calls = 0

    class ObservedProvider(MockEmbeddingProvider):
        async def embed(self, texts):
            nonlocal calls
            calls += 1
            return await super().embed(texts)

    monkeypatch.setattr("app.rag.service.get_embedding_provider", ObservedProvider)
    with Session(bound) as session:
        task = ImportJob(
            tenant_id=tenant_id, user_id=user_id, storage_path=path,
            source_name="secret.txt", content_type="text/plain",
        )
        session.add(task)
        session.commit()
        task_id = task.id
    await jobs.run_import_jobs_once(limit=1)
    with Session(bound) as session:
        persisted = session.get(ImportJob, task_id)
        assert persisted is not None and persisted.status == "failed"
        assert not list(session.exec(select(Document)).all())
    assert calls == 0


async def test_until_idle_waits_for_owned_capacity_and_pending_job(
    isolated_review, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound, tenant_id, user_id = isolated_review
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class HoldingProvider(MockEmbeddingProvider):
        async def embed(self, texts):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            return await super().embed(texts)

    monkeypatch.setattr("app.rag.service.get_embedding_provider", HoldingProvider)
    ids = []
    with Session(bound) as session:
        for filename in ("first.txt", "second.txt"):
            path = save_source_file(tenant_id, filename.encode(), filename)
            task = ImportJob(
                tenant_id=tenant_id, user_id=user_id, storage_path=path,
                source_name=filename, content_type="text/plain",
            )
            session.add(task)
            session.commit()
            ids.append(task.id)
    owner = asyncio.create_task(jobs.run_import_jobs_once(limit=1))
    waiter = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        waiter = asyncio.create_task(jobs.run_import_jobs_until_idle(limit=1))
        await asyncio.sleep(0.08)
        assert not waiter.done(), "capacity occupied must not be mistaken for idle"
        release.set()
        await asyncio.wait_for(asyncio.gather(owner, waiter), timeout=5)
    finally:
        release.set()
        pending = [task for task in (owner, waiter) if task is not None and not task.done()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    with Session(bound) as session:
        assert all(session.get(ImportJob, identity).status == "success" for identity in ids)
        assert len(session.exec(select(Document)).all()) == 2


async def test_cancelled_parallel_runner_releases_all_claims_and_recovers(
    isolated_review, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound, tenant_id, user_id = isolated_review
    monkeypatch.setattr(settings, "RAG_IMPORT_MAX_CONCURRENCY", 2)
    entered = asyncio.Event()
    hold = asyncio.Event()
    inflight = 0

    class CancellableProvider(MockEmbeddingProvider):
        async def embed(self, texts):
            nonlocal inflight
            inflight += 1
            if inflight == 2:
                entered.set()
            try:
                await hold.wait()
                return await super().embed(texts)
            finally:
                inflight -= 1

    monkeypatch.setattr("app.rag.service.get_embedding_provider", CancellableProvider)
    ids = []
    with Session(bound) as session:
        for filename in ("cancel-first.txt", "cancel-second.txt"):
            path = save_source_file(tenant_id, filename.encode(), filename)
            task = ImportJob(
                tenant_id=tenant_id, user_id=user_id, storage_path=path,
                source_name=filename, content_type="text/plain",
            )
            session.add(task)
            session.commit()
            ids.append(task.id)
    owner = asyncio.create_task(jobs.run_import_jobs_once(limit=2))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
    finally:
        hold.set()
        if not owner.done():
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
    assert inflight == 0
    with Session(bound) as session:
        assert not list(session.exec(select(Document)).all())
        assert all(session.get(ImportJob, identity).status != "success" for identity in ids)
    monkeypatch.setattr("app.rag.service.get_embedding_provider", MockEmbeddingProvider)
    await asyncio.wait_for(jobs.run_import_jobs_until_idle(limit=2), timeout=5)
    with Session(bound) as session:
        for identity in ids:
            persisted = session.get(ImportJob, identity)
            assert persisted is not None and persisted.status == "success"
            assert persisted.attempt_count == 2
        assert len(session.exec(select(Document)).all()) == 2


async def test_claim_commit_failure_does_not_leak_earlier_capacity(
    isolated_review, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound, tenant_id, user_id = isolated_review
    monkeypatch.setattr(settings, "RAG_IMPORT_MAX_CONCURRENCY", 2)
    ids = []
    with Session(bound) as session:
        for filename in ("claim-first.txt", "claim-second.txt"):
            path = save_source_file(tenant_id, filename.encode(), filename)
            task = ImportJob(
                tenant_id=tenant_id, user_id=user_id, storage_path=path,
                source_name=filename, content_type="text/plain",
            )
            session.add(task)
            session.commit()
            ids.append(task.id)
    original_commit = Session.commit
    injected = False

    def fail_second_claim(session):
        nonlocal injected
        second_running = any(
            isinstance(value, ImportJob) and value.id == ids[1] and value.status == "running"
            for value in session.identity_map.values()
        )
        if not injected and second_running:
            injected = True
            raise RuntimeError("isolated second-claim commit failure")
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", fail_second_claim)
    with pytest.raises(RuntimeError, match="second-claim"):
        await jobs.run_import_jobs_once(limit=2)
    assert injected
    monkeypatch.setattr(Session, "commit", original_commit)
    await asyncio.wait_for(jobs.run_import_jobs_until_idle(limit=2), timeout=1)
    with Session(bound) as session:
        assert all(session.get(ImportJob, identity).status == "success" for identity in ids)
        assert len(session.exec(select(Document)).all()) == 2


async def test_orphan_receipt_with_wrong_source_is_not_accepted(isolated_review) -> None:
    bound, tenant_id, user_id = isolated_review
    intended_path = save_source_file(tenant_id, b"intended recovery source", "intended.txt")
    unrelated_path = save_source_file(tenant_id, b"unrelated source", "unrelated.txt")
    with Session(bound) as session:
        unrelated = await RAGService(session, tenant_id).ingest_text(
            "unrelated source", title="unrelated", source="unrelated.txt",
            user_id=user_id, storage_path=unrelated_path,
        )
        unrelated_id = unrelated.id
        task = ImportJob(
            tenant_id=tenant_id, user_id=user_id, storage_path=intended_path,
            source_name="intended.txt", content_type="text/plain",
            status="running", attempt_count=1,
        )
        session.add(task)
        session.flush()
        unrelated.import_job_id = task.id
        session.add(unrelated)
        session.commit()
        task_id = task.id
    await jobs.run_import_jobs_until_idle(limit=1)
    with Session(bound) as session:
        persisted = session.get(ImportJob, task_id)
        assert persisted is not None and persisted.status == "failed"
        assert persisted.document_id != unrelated_id
        assert persisted.attempt_count == 1
        assert len(session.exec(select(Document)).all()) == 1


async def test_orphan_deleted_receipt_fails_without_replaying(isolated_review) -> None:
    bound, tenant_id, user_id = isolated_review
    path = save_source_file(tenant_id, b"deleted published original", "deleted.txt")
    with Session(bound) as session:
        task = ImportJob(
            tenant_id=tenant_id, user_id=user_id, storage_path=path,
            source_name="deleted.txt", content_type="text/plain",
        )
        session.add(task)
        session.commit()
        task_id = task.id
    await jobs.run_import_jobs_once(limit=1)
    with Session(bound) as session:
        task = session.get(ImportJob, task_id)
        document_id = task.document_id
        actor = session.get(User, user_id)
        assert await RAGService(session, tenant_id).delete_document(document_id, actor)
        task.status = "running"  # simulate the lost terminal-status window
        session.add(task)
        session.commit()
    await jobs.run_import_jobs_until_idle(limit=1)
    with Session(bound) as session:
        persisted = session.get(ImportJob, task_id)
        assert persisted is not None and persisted.status == "failed"
        assert persisted.attempt_count == 1
        assert len(session.exec(select(Document)).all()) == 1
        assert session.get(Document, document_id).deleted_at is not None


async def test_active_recovery_exclusion_and_engine_bound_capacity(
    isolated_review, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound, tenant_id, user_id = isolated_review
    other = create_engine(f"sqlite:///{Path(bound.url.database).with_name('other.db').as_posix()}")
    SQLModel.metadata.create_all(other)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class FirstHeldProvider(MockEmbeddingProvider):
        async def embed(self, texts):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            return await super().embed(texts)

    monkeypatch.setattr("app.rag.service.get_embedding_provider", FirstHeldProvider)
    ids = []
    for db in (bound, other):
        with Session(db) as session:
            if db is other:
                session.add(Tenant(id=tenant_id, name="other-database"))
                session.add(User(
                    id=user_id, tenant_id=tenant_id, username=uuid4().hex,
                    hashed_password="", role="tenant_admin", is_active=True,
                ))
            path = save_source_file(tenant_id, b"same source independent database", "same.txt")
            task = ImportJob(
                tenant_id=tenant_id, user_id=user_id, storage_path=path,
                source_name="same.txt", content_type="text/plain",
            )
            session.add(task)
            session.commit()
            ids.append(task.id)
    owner = asyncio.create_task(jobs.run_import_jobs_once(limit=1))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert jobs.recover_interrupted_import_jobs() == 0
        assert await jobs.run_import_jobs_once(limit=1) == 0
        with Session(bound) as session:
            active = session.get(ImportJob, ids[0])
            assert active.status == "running" and active.attempt_count == 1
        monkeypatch.setattr(jobs, "engine", other)
        assert await jobs.run_import_jobs_once(limit=1) == 1
        monkeypatch.setattr(jobs, "engine", bound)
        release.set()
        assert await asyncio.wait_for(owner, timeout=3) == 1
    finally:
        monkeypatch.setattr(jobs, "engine", bound)
        release.set()
        if not owner.done():
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
        other.dispose()
    for db, identity in zip((bound, other), ids, strict=True):
        with Session(db) as session:
            completed = session.get(ImportJob, identity)
            assert completed.status == "success" and completed.attempt_count == 1
            assert len(session.exec(select(Document)).all()) == 1
