"""Independent HTTP/domain acceptance for F07, F04, F05 and F06."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db
from app.api.routes.rag import router
from app.core.config import settings
from app.models.rag import Document, ImportBatch, ImportJob, ImportJobTrace
from app.models.user import User
from app.rag import import_jobs
from app.rag.document_storage import save_source_file
from tests import test_rag_039_acceptance as base


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "RAG_KB_SCOPE", "tenant")
    yield from base.isolated.__wrapped__(monkeypatch, tmp_path)


def actor(db, key):
    with Session(db) as session:
        user = session.get(User, key)
        assert user is not None
        return user


def members(db):
    with Session(db) as session:
        owner = session.get(User, "u-a")
        assert owner is not None
        owner.role = "member"
        session.add(owner)
        session.add(User(id="u-c", tenant_id="t-a", username="competing-member", hashed_password="", role="member"))
        session.add(User(id="u-admin", tenant_id="t-a", username="same-admin", hashed_password="", role="tenant_admin"))
        session.add(User(
            id="u-system", tenant_id="t-a", username="system-admin", hashed_password="", role="system_admin",
        ))
        session.commit()


def http(db, user_id):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: actor(db, user_id)

    def request_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_db] = request_session
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://acceptance.invalid")


def file_job(db, user_id="u-a", *, source="shared.txt", body=b"owner original synthetic knowledge", batch_id=None):
    with Session(db) as session:
        user = session.get(User, user_id)
        assert user is not None
        path = save_source_file(user.tenant_id, body, source)
        return import_jobs.create_upload_import_job(
            session, user, storage_path=path, filename=source, content_type="text/plain", batch_id=batch_id,
        ).id


async def submit(client, kind, body):
    if kind == "file":
        response = await client.post(
            "/api/rag/import-jobs/upload", files={"files": ("shared.txt", body, "text/plain")},
        )
        assert response.status_code == 202, response.text
        return response.json()["jobs"][0]["id"]
    response = await client.post("/api/rag/import-jobs/url", json={"url": "https://example.invalid/shared.txt"})
    assert response.status_code == 202, response.text
    return response.json()["id"]


@pytest.mark.parametrize("scope", ["tenant", "uploader"])
@pytest.mark.parametrize("kind", ["file", "url"])
@pytest.mark.parametrize("same", [True, False], ids=["same-content", "different-content"])
async def test_member_http_same_source_cannot_inherit_or_demote_owner(isolated, monkeypatch, scope, kind, same):
    db, provider = isolated
    members(db)
    monkeypatch.setattr(settings, "RAG_KB_SCOPE", scope)
    payload = b"owner original synthetic knowledge"

    async def fetch(_url):
        return payload, "text/plain"

    monkeypatch.setattr(import_jobs, "_fetch_remote_content", fetch)
    async with http(db, "u-a") as owner:
        original_job = await submit(owner, kind, payload)
        await import_jobs.run_import_jobs_until_idle(limit=1)
        original_id = next(job.document_id for job in base.persisted(db)[0] if job.id == original_job)
        old = base.persisted(db)
        calls = provider.calls
        if not same:
            payload = b"competing unauthorized replacement"
        async with http(db, "u-c") as competitor:
            denied = await competitor.post(f"/api/rag/documents/{original_id}/reparse")
            assert denied.status_code == 400
            new_job = await submit(competitor, kind, payload)
        await import_jobs.run_import_jobs_until_idle(limit=1)
        jobs, docs, chunks = base.persisted(db)
        assert next(job for job in jobs if job.id == new_job).status == "failed"
        assert provider.calls == calls
        assert len(docs) == 1 and docs[0].id == original_id and docs[0].is_current
        assert docs[0].version_group_id == old[1][0].version_group_id
        assert {chunk.id for chunk in chunks} == {chunk.id for chunk in old[2]}
        listed = await owner.get("/api/rag/documents")
        found = await owner.post("/api/rag/search", json={"query": "owner original synthetic knowledge"})
        assert listed.status_code == found.status_code == 200
        assert any(item["id"] == original_id for item in listed.json())
        assert any(item["document_id"] == original_id for item in found.json())


@pytest.mark.parametrize("kind", ["file", "url"])
@pytest.mark.parametrize("writer", ["u-a", "u-admin", "u-system"])
async def test_authorized_http_writer_can_upgrade_source_version(isolated, monkeypatch, kind, writer):
    db, _ = isolated
    members(db)
    payload = b"owner original synthetic knowledge"

    async def fetch(_url):
        return payload, "text/plain"

    monkeypatch.setattr(import_jobs, "_fetch_remote_content", fetch)
    async with http(db, "u-a") as owner:
        await submit(owner, kind, payload)
    await import_jobs.run_import_jobs_until_idle(limit=1)
    payload = b"authorized replacement synthetic knowledge"
    async with http(db, writer) as authorized:
        job_id = await submit(authorized, kind, payload)
    await import_jobs.run_import_jobs_until_idle(limit=1)
    jobs, docs, _ = base.persisted(db)
    assert next(job for job in jobs if job.id == job_id).status == "success"
    assert sorted(doc.version_number for doc in docs) == [1, 2]
    assert len({doc.version_group_id for doc in docs}) == 1
    assert sum(doc.is_current for doc in docs) == 1
    assert next(doc for doc in docs if doc.is_current).user_id == writer


@pytest.mark.parametrize("kind", ["file", "url"])
async def test_real_http_delete_then_same_content_upload_creates_searchable_current(isolated, monkeypatch, kind):
    db, _ = isolated
    members(db)
    payload = b"owner original synthetic knowledge"

    async def fetch(_url):
        return payload, "text/plain"

    monkeypatch.setattr(import_jobs, "_fetch_remote_content", fetch)
    async with http(db, "u-a") as owner:
        await submit(owner, kind, payload)
        await import_jobs.run_import_jobs_until_idle(limit=1)
        old_id = base.persisted(db)[1][0].id
        old_chunk_ids = {chunk.id for chunk in base.persisted(db)[2]}
        deleted = await owner.delete(f"/api/rag/documents/{old_id}")
        assert deleted.status_code == 200, deleted.text
        replacement = await submit(owner, kind, payload)
        await import_jobs.run_import_jobs_until_idle(limit=1)
        jobs, docs, chunks = base.persisted(db)
        finished = next(job for job in jobs if job.id == replacement)
        assert finished.status == "success", (finished.status, finished.error)
        new_id = finished.document_id
        assert new_id != old_id
        assert next(doc for doc in docs if doc.id == old_id).deleted_at is not None
        assert next(doc for doc in docs if doc.id == new_id).is_current
        assert old_chunk_ids.issubset({chunk.id for chunk in chunks})
        listed = await owner.get("/api/rag/documents")
        found = await owner.post("/api/rag/search", json={"query": "owner original synthetic knowledge"})
        assert listed.status_code == found.status_code == 200
        assert {item["id"] for item in listed.json()} == {new_id}
        assert any(item["document_id"] == new_id for item in found.json())
        assert all(item["document_id"] != old_id for item in found.json())


async def test_batch_declared_size_survives_committed_and_uncommitted_construction(isolated):
    db, _ = isolated
    with Session(db) as session:
        user = session.get(User, "u-a")
        assert user is not None
        batch = import_jobs.create_import_batch(session, user, total_jobs=2, source_type="file", commit=False)
        batch_id = batch.id
        path = save_source_file("t-a", b"first source", "first.txt")
        import_jobs.create_upload_import_job(
            session, user, storage_path=path, filename="first.txt", content_type="text/plain",
            batch_id=batch_id, commit=False,
        )
        assert batch.total_jobs == 2 and batch.status == "pending"
        with Session(db) as other:
            assert other.get(ImportBatch, batch_id) is None
            assert other.exec(select(ImportJob)).first() is None
        session.commit()
    await import_jobs.run_import_jobs_until_idle(limit=1)
    with Session(db) as session:
        batch = session.get(ImportBatch, batch_id)
        assert batch is not None
        assert (batch.status, batch.total_jobs, batch.completed_jobs) == ("running", 2, 1)
    file_job(db, source="second.txt", body=b"second source", batch_id=batch_id)
    await import_jobs.run_import_jobs_until_idle(limit=1)
    assert import_jobs.recover_interrupted_import_jobs() == 0
    with Session(db) as session:
        batch = session.get(ImportBatch, batch_id)
        assert batch is not None
        assert (batch.status, batch.total_jobs, batch.completed_jobs, batch.successful_jobs) == ("success", 2, 2, 2)


@pytest.mark.parametrize("kind", ["file", "url"])
async def test_wrong_subject_creation_cannot_commit_into_existing_batch(isolated, kind):
    db, _ = isolated
    members(db)
    with Session(db) as session:
        batch = import_jobs.create_import_batch(session, actor(db, "u-a"), total_jobs=2, source_type=kind)
        batch_id = batch.id
    for writer in ("u-c", "u-b"):
        with Session(db) as session:
            user = actor(db, writer)
            with pytest.raises(ValueError):
                if kind == "url":
                    import_jobs.create_url_import_job(
                        session, user, url="https://example.invalid/wrong.txt", batch_id=batch_id,
                    )
                else:
                    path = save_source_file(user.tenant_id, b"bad binding", "wrong.txt")
                    import_jobs.create_upload_import_job(
                        session, user, storage_path=path, filename="wrong.txt", content_type="text/plain",
                        batch_id=batch_id,
                    )
    with Session(db) as session:
        assert session.exec(select(ImportJob)).first() is None
        batch = session.get(ImportBatch, batch_id)
        assert batch is not None and batch.total_jobs == 2 and batch.status == "pending"


@pytest.mark.parametrize("window", ["after-ack", "after-cancel", "before-commit"])
async def test_dedupe_commit_windows_preserve_only_real_persisted_success(isolated, monkeypatch, window):
    db, provider = isolated
    first = file_job(db)
    await import_jobs.run_import_jobs_until_idle(limit=1)
    target = file_job(db)
    calls = provider.calls
    original = Session.commit
    fired = False
    observed = []

    def commit_fault(session):
        nonlocal fired
        if not fired and any(isinstance(row, ImportJob) and row.id == target and row.status == "success"
                             for row in session.dirty):
            fired = True
            if window != "before-commit":
                original(session)
                with Session(db) as observer:
                    stored = observer.get(ImportJob, target)
                    assert stored is not None
                    observed.append((stored.status, stored.attempt_count, stored.document_id))
            if window == "after-cancel":
                raise asyncio.CancelledError
            raise OSError("synthetic acknowledgement failure")
        return original(session)

    with monkeypatch.context() as fault:
        fault.setattr(Session, "commit", commit_fault)
        if window == "after-cancel":
            with pytest.raises(asyncio.CancelledError):
                await import_jobs.run_import_jobs_once(limit=1)
        else:
            await import_jobs.run_import_jobs_once(limit=1)
    assert fired
    with Session(db) as session:
        stored = session.get(ImportJob, target)
        predecessor = session.get(ImportJob, first)
        assert stored is not None and predecessor is not None
        assert stored.status == ("failed" if window == "before-commit" else "success")
        assert stored.attempt_count == 1
        traces = session.exec(select(ImportJobTrace).where(ImportJobTrace.job_id == target)).all()
        if window != "before-commit":
            assert observed == [("success", 1, predecessor.document_id)]
            assert stored.document_id == predecessor.document_id and traces == []
    import_jobs.recover_interrupted_import_jobs()
    await import_jobs.run_import_jobs_until_idle(limit=1)
    assert provider.calls == calls
    assert len(base.persisted(db)[1]) == 1


async def test_retry_of_failed_child_preserves_missing_sibling_and_declared_total(isolated):
    db, _ = isolated
    with Session(db) as session:
        batch_id = import_jobs.create_import_batch(session, actor(db, "u-a"), total_jobs=2, source_type="file").id
    key = file_job(db, batch_id=batch_id)
    with Session(db) as session:
        job = session.get(ImportJob, key)
        assert job is not None
        old_path = job.storage_path
        job.storage_path = "data/knowledge/t-a/absent.txt"
        session.add(job)
        session.commit()
    await import_jobs.run_import_jobs_until_idle(limit=1)
    with Session(db) as session:
        batch = session.get(ImportBatch, batch_id)
        job = session.get(ImportJob, key)
        assert batch is not None and job is not None
        assert (batch.status, batch.total_jobs, batch.completed_jobs) == ("running", 2, 1)
        job.storage_path = old_path
        session.add(job)
        session.commit()
        import_jobs.retry_import_job(session, actor(db, "u-a"), key)
    await import_jobs.run_import_jobs_until_idle(limit=1)
    with Session(db) as session:
        batch = session.get(ImportBatch, batch_id)
        assert batch is not None
        assert (batch.status, batch.total_jobs, batch.completed_jobs, batch.successful_jobs) == ("running", 2, 1, 1)


@pytest.mark.parametrize("writer", ["u-a", "u-admin", "u-system"])
async def test_authorized_same_content_upload_remains_idempotent(isolated, writer):
    db, provider = isolated
    members(db)
    first = file_job(db)
    await import_jobs.run_import_jobs_until_idle(limit=1)
    original = base.persisted(db)
    calls = provider.calls
    async with http(db, writer) as authorized:
        key = await submit(authorized, "file", b"owner original synthetic knowledge")
    await import_jobs.run_import_jobs_until_idle(limit=1)
    jobs, docs, chunks = base.persisted(db)
    old_id = next(job.document_id for job in jobs if job.id == first)
    deduplicated = next(job for job in jobs if job.id == key)
    assert deduplicated.status == "success" and deduplicated.document_id == old_id
    assert provider.calls == calls and len(docs) == 1
    assert {chunk.id for chunk in chunks} == {chunk.id for chunk in original[2]}


@pytest.mark.parametrize("defect", ["foreign-owner", "source", "hash", "deleted"])
async def test_ack_success_evidence_is_not_trusted_after_document_binding_corruption(isolated, monkeypatch, defect):
    from datetime import UTC, datetime

    db, _ = isolated
    members(db)
    file_job(db)
    await import_jobs.run_import_jobs_until_idle(limit=1)
    target = file_job(db)
    original = Session.commit
    fired = False

    def corrupt_after_real_commit(session):
        nonlocal fired
        if not fired and any(isinstance(row, ImportJob) and row.id == target and row.status == "success"
                             for row in session.dirty):
            fired = True
            original(session)
            with Session(db) as other:
                job = other.get(ImportJob, target)
                assert job is not None and job.status == "success"
                doc = other.get(Document, job.document_id)
                assert doc is not None
                if defect == "foreign-owner":
                    doc.user_id = "u-c"
                elif defect == "source":
                    doc.source = "wrong-source.txt"
                elif defect == "hash":
                    doc.content_hash = "different-hash"
                else:
                    doc.deleted_at = datetime.now(UTC)
                other.add(doc)
                original(other)
            raise OSError("synthetic acknowledgement failure after binding corruption")
        return original(session)

    with monkeypatch.context() as fault:
        fault.setattr(Session, "commit", corrupt_after_real_commit)
        await import_jobs.run_import_jobs_once(limit=1)
    assert fired
    with Session(db) as session:
        job = session.get(ImportJob, target)
        assert job is not None and job.status == "failed" and job.attempt_count == 1
    assert import_jobs.recover_interrupted_import_jobs() == 0
    assert await import_jobs.run_import_jobs_until_idle(limit=1) == 0


async def test_empty_and_corrupted_batch_normal_completion_never_aggregate_foreign_child(isolated):
    db, _ = isolated
    with Session(db) as session:
        user = actor(db, "u-a")
        empty = import_jobs.create_import_batch(session, user, total_jobs=0, source_type="file").id
        target = import_jobs.create_import_batch(session, user, total_jobs=2, source_type="file").id
    legitimate = file_job(db, source="legitimate.txt", batch_id=target)
    with Session(db) as session:
        session.add(ImportJob(
            tenant_id="t-b", user_id="u-b", batch_id=target, status="success",
            source_type="file", source_name="foreign.txt", attempt_count=1,
        ))
        session.commit()
        before = session.get(ImportBatch, target)
        assert before is not None
        fields = (before.status, before.total_jobs, before.completed_jobs, before.successful_jobs, before.failed_jobs)
    await import_jobs.run_import_jobs_until_idle(limit=1)
    with Session(db) as session:
        job = session.get(ImportJob, legitimate)
        current = session.get(ImportBatch, target)
        blank = session.get(ImportBatch, empty)
        assert job is not None and job.status == "success"
        assert current is not None and blank is not None
        assert (current.status, current.total_jobs, current.completed_jobs,
                current.successful_jobs, current.failed_jobs) == fields
        assert blank.status == "pending" and blank.total_jobs == 0 and blank.completed_jobs == 0
