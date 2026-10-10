"""RAG-039: E2 isolated SQLite import lifecycle, synthetic embedding only."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.core.config import settings
from app.core.security import Role
from app.models.rag import Document, ImportJob
from app.models.user import Tenant, User
from app.rag import import_jobs as jobs
from app.rag.document_storage import save_source_file
from app.rag.embeddings.factory import set_embedding_override
from app.rag.embeddings.mock import MockEmbeddingProvider


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    import app.rag.document_storage as storage
    db = create_engine(
        f"sqlite:///{tmp_path / 'lifecycle.db'}", connect_args={"check_same_thread": False, "timeout": 1}
    )
    SQLModel.metadata.create_all(db)
    monkeypatch.setattr(jobs, "engine", db)
    monkeypatch.setattr(storage, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "local")
    monkeypatch.setattr(settings, "RAG_CHUNK_STRATEGY", "structured")
    monkeypatch.setattr(settings, "RAG_IMPORT_MAX_CONCURRENCY", 2)
    set_embedding_override(MockEmbeddingProvider())
    yield db
    set_embedding_override(None)
    db.dispose()


def user(tenant=None):
    return User(id=uuid4().hex, tenant_id=tenant or uuid4().hex, username=uuid4().hex,
                hashed_password="", role=Role.TENANT_ADMIN.value, is_active=True)


def upload(db, actor, filename="book.txt", body=b"Knowledge import lifecycle proof", batch=None):
    path = save_source_file(actor.tenant_id, body, filename)
    with Session(db) as session:
        result = jobs.create_upload_import_job(session, actor, storage_path=path, filename=filename,
                                              content_type="text/plain", batch_id=batch)
        return result.id


def state(db, job_id):
    with Session(db) as session:
        return session.get(ImportJob, job_id).model_dump()


@pytest.mark.parametrize("invalid", [0, -1, True, float("nan"), float("inf"), 1001])
async def test_invalid_batch_limit_has_no_side_effect(isolated, invalid):
    job_id = upload(isolated, user())
    with pytest.raises(ValueError):
        await jobs.run_import_jobs_once(limit=invalid)
    assert state(isolated, job_id)["attempt_count"] == 0
    assert state(isolated, job_id)["status"] == "pending"


async def test_concurrency_is_independent_from_batch_size(isolated):
    actor = user()
    ids = [upload(isolated, actor, f"book{i}.txt") for i in range(5)]
    active = peak = 0
    class Delayed(MockEmbeddingProvider):
        async def embed(self, texts):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.02)
            vectors = await super().embed(texts)
            active -= 1
            return vectors
    set_embedding_override(Delayed())
    assert await jobs.run_import_jobs_once(limit=5) == 5
    assert peak == 2
    assert all(state(isolated, key)["status"] == "success" for key in ids)


async def test_orphan_running_recovery(isolated):
    job_id = upload(isolated, user())
    with Session(isolated) as session:
        job = session.get(ImportJob, job_id)
        job.status, job.attempt_count = "running", 1
        session.add(job)
        session.commit()
    assert await jobs.run_import_jobs_once(limit=1) == 1
    assert state(isolated, job_id)["status"] == "success"
    assert state(isolated, job_id)["attempt_count"] == 2


async def test_duplicate_sources_and_concurrent_runners_publish_once(isolated):
    actor = user()
    ids = [upload(isolated, actor) for _ in range(3)]
    await asyncio.gather(jobs.run_import_jobs_until_idle(limit=3), jobs.run_import_jobs_until_idle(limit=3))
    with Session(isolated) as session:
        docs = session.exec(select(Document).where(col(Document.tenant_id) == actor.tenant_id)).all()
        assert len(docs) == 1
    assert all(state(isolated, key)["status"] == "success" for key in ids)
    assert all(state(isolated, key)["attempt_count"] == 1 for key in ids)


def test_threads_do_not_duplicate_claim(isolated):
    key = upload(isolated, user())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: asyncio.run(jobs.run_import_jobs_until_idle(limit=1)), range(2)))
    assert sum(results) == 1
    assert state(isolated, key)["attempt_count"] == 1
    assert state(isolated, key)["status"] == "success"


async def test_cross_tenant_storage_rejected_before_read(isolated, monkeypatch):
    actor, victim = user(), user()
    victim_id = upload(isolated, victim)
    with Session(isolated) as session:
        target = session.get(ImportJob, victim_id)
        cross = jobs.create_upload_import_job(session, actor, storage_path=target.storage_path,
                                             filename="victim.txt", content_type="text/plain")
        target.status = "failed"
        session.add(target)
        session.commit()
        key = cross.id
    reads = []
    monkeypatch.setattr(jobs, "read_source_file", lambda path: reads.append(path) or b"secret")
    await jobs.run_import_jobs_once(limit=1)
    assert state(isolated, key)["status"] == "failed"
    assert reads == []


async def test_exhausted_orphan_does_not_retry(isolated):
    key = upload(isolated, user())
    with Session(isolated) as session:
        job = session.get(ImportJob, key)
        job.status, job.attempt_count = "running", 3
        session.add(job)
        session.commit()
    assert await jobs.run_import_jobs_once(limit=1) == 0
    assert state(isolated, key)["status"] == "failed"
    assert state(isolated, key)["attempt_count"] == 3


async def test_cancellation_releases_active_and_recovers(isolated):
    key = upload(isolated, user())
    entered = asyncio.Event()
    class Blocked(MockEmbeddingProvider):
        async def embed(self, texts):
            entered.set()
            await asyncio.Event().wait()
    set_embedding_override(Blocked())
    task = asyncio.create_task(jobs.run_import_jobs_once(limit=1))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    set_embedding_override(MockEmbeddingProvider())
    assert await jobs.run_import_jobs_once(limit=1) == 1
    assert state(isolated, key)["status"] == "success"


async def test_real_tenant_url_quota_reservation_releases_sql_lock(isolated, monkeypatch):
    actor = user()
    with Session(isolated) as session:
        session.add(Tenant(id=actor.tenant_id, name="quota", storage_limit_bytes=10000))
        session.commit()
        keys = [jobs.create_url_import_job(session, actor, url=f"https://fixture.invalid/{n}.txt").id for n in range(2)]
    async def fetch(url):
        return b"URL snapshot immutable content", "text/plain"
    monkeypatch.setattr(jobs, "_fetch_remote_content", fetch)
    active = peak = 0
    class Delayed(MockEmbeddingProvider):
        async def embed(self, texts):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.02)
            active -= 1
            return await super().embed(texts)
    set_embedding_override(Delayed())
    assert await jobs.run_import_jobs_once(limit=2) == 2
    assert all(state(isolated, key)["status"] == "success" for key in keys)
    assert peak == 2


async def test_published_upload_receipt_survives_post_commit_cancel(isolated, monkeypatch):
    from app.rag.service import RAGService
    actor = user()
    key = upload(isolated, actor)
    original = RAGService.ingest_parsed_document
    async def committed_then_cancel(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise asyncio.CancelledError()
    monkeypatch.setattr(RAGService, "ingest_parsed_document", committed_then_cancel)
    with pytest.raises(asyncio.CancelledError):
        await jobs.run_import_jobs_once(limit=1)
    assert state(isolated, key)["status"] == "success"
    assert await jobs.run_import_jobs_once(limit=1) == 0
    with Session(isolated) as session:
        docs = session.exec(select(Document).where(col(Document.tenant_id) == actor.tenant_id)).all()
        assert len(docs) == 1
        import app.rag.document_storage as storage
        assert storage.resolve_source_file_path(docs[0].storage_path).exists()


async def test_reparse_final_commit_receipt_is_atomic(isolated, monkeypatch):
    from app.rag.service import RAGService
    actor = user()
    seed = upload(isolated, actor)
    await jobs.run_import_jobs_once(limit=1)
    with Session(isolated) as session:
        key = jobs.create_reparse_job(session, actor, state(isolated, seed)["document_id"]).id
    original = RAGService.reindex_document_in_place
    async def committed_then_cancel(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise asyncio.CancelledError()
    monkeypatch.setattr(RAGService, "reindex_document_in_place", committed_then_cancel)
    with pytest.raises(asyncio.CancelledError):
        await jobs.run_import_jobs_once(limit=1)
    assert state(isolated, key)["status"] == "success"
    assert await jobs.run_import_jobs_once(limit=1) == 0
    with Session(isolated) as session:
        assert len(session.exec(select(Document).where(col(Document.tenant_id) == actor.tenant_id)).all()) == 1


async def test_reparse_failed_embedding_preserves_old_search_path(isolated):
    from app.rag.service import RAGService
    actor = user()
    seed = upload(isolated, actor, body=b"KEEPOLDMARKER old current searchable content")
    await jobs.run_import_jobs_once(limit=1)
    doc_id = state(isolated, seed)["document_id"]
    with Session(isolated) as session:
        key = jobs.create_reparse_job(session, actor, doc_id).id
    class Failed(MockEmbeddingProvider):
        async def embed(self, texts):
            raise RuntimeError("isolated embedding failure")
    set_embedding_override(Failed())
    await jobs.run_import_jobs_once(limit=1)
    assert state(isolated, key)["status"] == "failed"
    set_embedding_override(MockEmbeddingProvider())
    with Session(isolated) as session:
        result = await RAGService(session, actor.tenant_id).search("KEEPOLDMARKER")
        assert result and any(row.document_id == doc_id for row in result)


async def test_url_orphan_snapshot_is_reused_without_fetch(isolated, monkeypatch):
    actor = user()
    path = save_source_file(actor.tenant_id, b"persisted URL snapshot before restart", "persisted.txt")
    with Session(isolated) as session:
        job = jobs.create_url_import_job(session, actor, url="https://fixture.invalid/persisted.txt")
        job.storage_path, job.status, job.attempt_count = path, "running", 1
        job.content_type = "text/plain"
        session.add(job)
        session.commit()
        key = job.id
    async def forbidden_fetch(url):
        pytest.fail("persisted snapshot must not refetch")
    monkeypatch.setattr(jobs, "_fetch_remote_content", forbidden_fetch)
    await jobs.run_import_jobs_once(limit=1)
    assert state(isolated, key)["status"] == "success"
    assert state(isolated, key)["storage_path"] == path


async def test_url_reparse_fetches_fresh_snapshot_and_preserves_old_source(isolated, monkeypatch):
    import app.rag.document_storage as storage
    actor = user()
    async def fetch(url):
        return b"URL original version", "text/plain"
    monkeypatch.setattr(jobs, "_fetch_remote_content", fetch)
    with Session(isolated) as session:
        seed = jobs.create_url_import_job(session, actor, url="https://fixture.invalid/article.txt").id
    await jobs.run_import_jobs_once(limit=1)
    old = state(isolated, seed)
    with Session(isolated) as session:
        key = jobs.create_reparse_job(session, actor, old["document_id"]).id
    calls = []
    async def fresh(url):
        calls.append(url)
        return b"URL refreshed text", "text/plain"
    monkeypatch.setattr(jobs, "_fetch_remote_content", fresh)
    await jobs.run_import_jobs_once(limit=1)
    assert calls == ["https://fixture.invalid/article.txt"]
    assert state(isolated, key)["status"] == "success"
    assert storage.resolve_source_file_path(old["storage_path"]).exists()
    with Session(isolated) as session:
        target = session.get(Document, old["document_id"])
        assert target.storage_path != old["storage_path"]
        assert storage.read_source_file(target.storage_path) == b"URL refreshed text"

def test_fresh_python_process_recovers_persisted_running_import(isolated):
    import os
    import subprocess
    import sys

    import app.rag.document_storage as storage

    actor = user()
    key = upload(isolated, actor, body=b"fresh process durable recovery source")
    with Session(isolated) as session:
        job = session.get(ImportJob, key)
        job.status, job.attempt_count = "running", 1
        session.add(job)
        session.commit()
    child_env = dict(os.environ)
    child_env.update({
        "DATABASE_URL": str(isolated.url), "ENV": "development",
        "EMBEDDING_PROVIDER": "mock", "EMBEDDING_DIM": "256",
        "EMBEDDING_API_KEY": "", "LLM_API_KEY": "", "OPENAI_API_KEY": "", "DEEPSEEK_API_KEY": "",
        "RAG_VECTOR_STORE": "local", "RAG_CHUNK_STRATEGY": "structured",
        "RAG_IMPORT_BATCH_SIZE": "2", "RAG_IMPORT_MAX_CONCURRENCY": "2",
        "RAG_IMPORT_ENABLED": "false", "RAG_CHUNK_LLM_BOUNDARY_ENABLED": "false",
        "RAG_SECTION_SUMMARY_ENABLED": "false",
    })
    script = """
import asyncio, sys
from pathlib import Path
from app.rag import document_storage, import_jobs
from app.rag.embeddings.factory import set_embedding_override
from app.rag.embeddings.mock import MockEmbeddingProvider
document_storage._PROJECT_ROOT = Path(sys.argv[1])
set_embedding_override(MockEmbeddingProvider())
assert asyncio.run(import_jobs.run_import_jobs_until_idle(limit=2)) == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(storage._PROJECT_ROOT)],
        env=child_env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    with Session(isolated) as session:
        restored = session.get(ImportJob, key)
        assert restored.status == "success" and restored.attempt_count == 2
        documents = session.exec(select(Document).where(col(Document.import_job_id) == key)).all()
        assert len(documents) == 1 and documents[0].chunk_count > 0
