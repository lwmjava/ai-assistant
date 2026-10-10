"""Observed regressions F07/F04/F05/F06: isolated real lifecycle, synthetic vectors."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.models.rag import Document, ImportBatch, ImportJob, ImportJobTrace
from app.models.user import User
from app.rag import import_jobs as jobs
from app.rag.document_storage import save_source_file
from app.rag.service import RAGService
from tests import test_rag_039_acceptance as support


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    yield from support.isolated.__wrapped__(monkeypatch, tmp_path)


def actors(db, role="member"):
    with Session(db) as session:
        owner = session.get(User, "u-a")
        owner.role = "member"
        session.add(owner)
        actor = User(id="u-c", tenant_id="t-a", username="synthetic-c", hashed_password="", role=role)
        session.add(actor)
        session.commit()
        session.refresh(actor)
        return actor


def create(db, actor_id="u-a", *, kind="file", text="original synthetic knowledge", batch=None, commit=True):
    with Session(db) as session:
        actor = session.get(User, actor_id)
        if kind == "url":
            return jobs.create_url_import_job(
                session, actor, url="https://synthetic.invalid/shared.txt", batch_id=batch,
            ).id
        path = save_source_file(actor.tenant_id, text.encode(), "shared.txt")
        key = jobs.create_upload_import_job(session, actor, storage_path=path, filename="shared.txt",
                                            content_type="text/plain", batch_id=batch, commit=commit).id
        if not commit:
            session.commit()
        return key


@pytest.mark.parametrize("scope", ["tenant", "uploader"])
@pytest.mark.parametrize("kind", ["file", "url"])
@pytest.mark.parametrize("changed", [False, True])
async def test_member_cannot_replace_or_dedupe_other_uploaders_source(isolated, monkeypatch, scope, kind, changed):
    db, provider = isolated
    actors(db)
    monkeypatch.setattr(settings, "RAG_KB_SCOPE", scope)
    content = b"original synthetic knowledge"
    async def fetch(url):
        return content, "text/plain"
    monkeypatch.setattr(jobs, "_fetch_remote_content", fetch)
    original_job = create(db, kind=kind)
    await jobs.run_import_jobs_until_idle(limit=1)
    before_jobs, before_docs, before_chunks = support.persisted(db)
    original_id = before_docs[0].id
    calls = provider.calls
    replacement = "replacement synthetic knowledge" if changed else "original synthetic knowledge"
    content = replacement.encode()
    other_job = create(db, "u-c", kind=kind, text=replacement)
    await jobs.run_import_jobs_until_idle(limit=1)
    with Session(db) as session:
        task = session.get(ImportJob, other_job)
        original = session.get(Document, original_id)
        owner = session.get(User, "u-a")
        assert task.status == "failed" and task.document_id is None
        assert original.is_current and original.deleted_at is None
        assert session.get(ImportJob, original_job).status == "success"
        assert [doc.id for doc in RAGService(session, "t-a").list_documents(owner)] == [original_id]
        hits = await RAGService(session, "t-a", reader=owner).search("original synthetic knowledge")
        assert any(hit.document_id == original_id for hit in hits)
    final_jobs, final_docs, final_chunks = support.persisted(db)
    assert len(final_jobs) == 2 and len(final_docs) == 1
    assert {row.id for row in final_chunks} == {row.id for row in before_chunks}
    # Actual search embeds a query; candidate rejection must not embed a new document.
    assert provider.calls == calls + 1
    assert not jobs._registry().active


@pytest.mark.parametrize("role", ["tenant_admin", "system_admin"])
@pytest.mark.parametrize("changed", [False, True])
async def test_existing_admin_authority_can_dedupe_or_upgrade_source(isolated, role, changed):
    db, _ = isolated
    actors(db, role)
    first = create(db)
    await jobs.run_import_jobs_until_idle(limit=1)
    second = create(db, "u-c", text="replacement synthetic" if changed else "original synthetic knowledge")
    await jobs.run_import_jobs_until_idle(limit=1)
    with Session(db) as session:
        before = session.get(Document, session.get(ImportJob, first).document_id)
        after = session.get(Document, session.get(ImportJob, second).document_id)
        assert session.get(ImportJob, second).status == "success"
        assert before.version_group_id == after.version_group_id
        assert after.is_current
        assert (before.id != after.id) == changed
        assert before.is_current == (not changed)


def batch_state(db, key):
    with Session(db) as session:
        batch = session.get(ImportBatch, key)
        return (batch.status, batch.total_jobs, batch.completed_jobs, batch.successful_jobs, batch.failed_jobs)


@pytest.mark.parametrize("commit", [False, True])
async def test_batch_construction_and_execution_keep_declared_total(isolated, commit):
    db, _ = isolated
    with Session(db) as session:
        actor = session.get(User, "u-a")
        batch = jobs.create_import_batch(session, actor, total_jobs=2, source_type="file", commit=commit)
        key = batch.id
        path = save_source_file("t-a", b"first synthetic content", "one.txt")
        first = jobs.create_upload_import_job(session, actor, storage_path=path, filename="one.txt",
                                             content_type="text/plain", batch_id=key, commit=commit).id
        if not commit:
            with Session(db) as fresh:
                assert fresh.get(ImportBatch, key) is None and fresh.get(ImportJob, first) is None
            session.commit()
    assert batch_state(db, key) == ("pending", 2, 0, 0, 0)
    await jobs.run_import_jobs_until_idle(limit=1)
    assert batch_state(db, key) == ("running", 2, 1, 1, 0)
    support.upload(db, source="two.txt", text="second synthetic content", batch=key)
    await jobs.run_import_jobs_until_idle(limit=1)
    assert batch_state(db, key) == ("success", 2, 2, 2, 0)


@pytest.mark.parametrize("kind", ["file", "url"])
@pytest.mark.parametrize("binding", ["tenant", "user", "missing"])
def test_batch_creation_rejects_invalid_binding_before_persistence(isolated, kind, binding):
    db, _ = isolated
    actors(db)
    with Session(db) as session:
        owner = session.get(User, "u-a")
        key = jobs.create_import_batch(session, owner, total_jobs=2, source_type=kind).id
    actor = "u-b" if binding == "tenant" else "u-c"
    target = "missing-batch" if binding == "missing" else key
    with pytest.raises(ValueError, match="批次"):
        create(db, actor, kind=kind, batch=target)
    with Session(db) as session:
        assert list(session.exec(select(ImportJob)).all()) == []
    assert batch_state(db, key) == ("pending", 2, 0, 0, 0)


@pytest.mark.parametrize("binding", ["tenant", "user"])
async def test_normal_completion_does_not_count_foreign_batch_child(isolated, binding):
    db, _ = isolated
    with Session(db) as session:
        actor = session.get(User, "u-a")
        key = jobs.create_import_batch(session, actor, total_jobs=2, source_type="file").id
    first = support.upload(db, source="one.txt", batch=key)
    second = support.upload(db, source="two.txt", batch=key)
    with Session(db) as session:
        foreign = session.get(ImportJob, second)
        setattr(foreign, "tenant_id" if binding == "tenant" else "user_id", "foreign-binding")
        foreign.status = "success"
        session.add(foreign)
        session.commit()
    before = batch_state(db, key)
    await jobs.run_import_jobs_until_idle(limit=1)
    with Session(db) as session:
        assert session.get(ImportJob, first).status == "success"
    assert batch_state(db, key) == before


def test_zero_declared_empty_batch_does_not_finish_success(isolated):
    db, _ = isolated
    with Session(db) as session:
        actor = session.get(User, "u-a")
        batch = jobs.create_import_batch(session, actor, total_jobs=0, source_type="file")
        key = batch.id
        jobs._recompute_batch(session, batch)
        session.commit()
    assert batch_state(db, key) == ("pending", 0, 0, 0, 0)


@pytest.mark.parametrize("kind", ["file", "url"])
@pytest.mark.parametrize("changed", [False, True])
async def test_new_import_after_soft_delete_publishes_searchable_document(isolated, monkeypatch, kind, changed):
    db, _ = isolated
    content = b"original synthetic knowledge"
    async def fetch(url):
        return content, "text/plain"
    monkeypatch.setattr(jobs, "_fetch_remote_content", fetch)
    first = create(db, kind=kind)
    await jobs.run_import_jobs_until_idle(limit=1)
    with Session(db) as session:
        original = session.get(ImportJob, first).document_id
        actor = session.get(User, "u-a")
        assert await RAGService(session, "t-a").delete_document(original, actor)
    replacement = "replacement synthetic knowledge" if changed else "original synthetic knowledge"
    content = replacement.encode()
    second = create(db, kind=kind, text=replacement)
    await jobs.run_import_jobs_until_idle(limit=1)
    with Session(db) as session:
        task = session.get(ImportJob, second)
        assert task.status == "success" and task.document_id != original
        before = session.get(Document, original)
        after = session.get(Document, task.document_id)
        assert before.deleted_at is not None and after.deleted_at is None and after.is_current
        assert after.version_group_id != before.version_group_id
        actor = session.get(User, "u-a")
        hits = await RAGService(session, "t-a", reader=actor).search(replacement)
        assert any(hit.document_id == after.id for hit in hits)
        assert all(hit.document_id != original for hit in hits)


@pytest.mark.parametrize("mode", ["dedupe", "publish", "reparse"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_durable_success_survives_lost_commit_ack(isolated, monkeypatch, mode, cancelled):
    db, provider = isolated
    first = create(db)
    await jobs.run_import_jobs_until_idle(limit=1)
    if mode == "reparse":
        with Session(db) as session:
            key = jobs.create_reparse_job(session, session.get(User, "u-a"),
                                          session.get(ImportJob, first).document_id).id
    else:
        key = create(db, text="new publication synthetic" if mode == "publish" else "original synthetic knowledge")
    calls = provider.calls
    commit = Session.commit
    observed = []
    def lose_ack(session):
        hit = not observed and any(isinstance(row, ImportJob) and row.id == key and row.status == "success"
                                  for row in session.dirty)
        commit(session)
        if hit:
            with Session(db) as fresh:
                task = fresh.get(ImportJob, key)
                observed.append((task.status, task.attempt_count, task.document_id))
            if cancelled:
                raise asyncio.CancelledError()
            raise OSError("synthetic durable terminal acknowledgement failure")
    with monkeypatch.context() as fault:
        fault.setattr(Session, "commit", lose_ack)
        try:
            await jobs.run_import_jobs_once(limit=1)
        except (OSError, asyncio.CancelledError):
            pass  # Acknowledgement failure may propagate; committed business truth must remain success.
    assert observed and observed[0][:2] == ("success", 1)
    with Session(db) as session:
        task = session.get(ImportJob, key)
        assert task.status == "success" and task.attempt_count == 1 and task.error is None
        assert task.document_id == observed[0][2]
        assert session.exec(select(ImportJobTrace).where(ImportJobTrace.job_id == key)).all() == []
        if mode == "dedupe":
            assert session.get(Document, task.document_id).import_job_id == first
    before = support.persisted(db)
    assert jobs.recover_interrupted_import_jobs() == 0
    assert await jobs.run_import_jobs_until_idle(limit=1) == 0
    after = support.persisted(db)
    assert {doc.id for doc in after[1]} == {doc.id for doc in before[1]}
    assert {chunk.id for chunk in after[2]} == {chunk.id for chunk in before[2]}
    assert provider.calls == calls + (0 if mode == "dedupe" else 1)
    assert not jobs._registry().active


async def test_uncommitted_dedupe_success_is_failed_and_retry_is_idempotent(isolated, monkeypatch):
    db, provider = isolated
    create(db)
    await jobs.run_import_jobs_until_idle(limit=1)
    key = create(db)
    commit = Session.commit
    fired = False
    def fail_before_commit(session):
        nonlocal fired
        if not fired and any(isinstance(row, ImportJob) and row.id == key and row.status == "success"
                             for row in session.dirty):
            fired = True
            raise OSError("synthetic before terminal commit")
        return commit(session)
    with monkeypatch.context() as fault:
        fault.setattr(Session, "commit", fail_before_commit)
        await jobs.run_import_jobs_until_idle(limit=1)
    calls = provider.calls
    with Session(db) as session:
        task = session.get(ImportJob, key)
        assert task.status == "failed" and task.attempt_count == 1
        jobs.retry_import_job(session, session.get(User, "u-a"), key)
    await jobs.run_import_jobs_until_idle(limit=1)
    with Session(db) as session:
        task = session.get(ImportJob, key)
        assert task.status == "success" and task.attempt_count == 2
    assert len(support.persisted(db)[1]) == 1 and provider.calls == calls


@pytest.mark.parametrize("role", ["tenant_admin", "system_admin"])
async def test_admin_deduplication_success_uses_legal_old_marker(isolated, monkeypatch, role):
    db, provider = isolated
    actors(db, role)
    first = create(db)
    await jobs.run_import_jobs_until_idle(limit=1)
    key = create(db, "u-c")
    calls = provider.calls
    commit = Session.commit
    fired = False
    def lose_ack(session):
        nonlocal fired
        hit = not fired and any(isinstance(row, ImportJob) and row.id == key and row.status == "success"
                                for row in session.dirty)
        commit(session)
        if hit:
            fired = True
            raise OSError("synthetic admin dedupe acknowledgement loss")
    with monkeypatch.context() as fault:
        fault.setattr(Session, "commit", lose_ack)
        await jobs.run_import_jobs_until_idle(limit=1)
    with Session(db) as session:
        task = session.get(ImportJob, key)
        assert task.status == "success" and task.attempt_count == 1
        assert session.get(Document, task.document_id).import_job_id == first
        assert session.exec(select(ImportJobTrace).where(ImportJobTrace.job_id == key)).all() == []
    assert len(support.persisted(db)[1]) == 1 and provider.calls == calls and fired


@pytest.mark.parametrize("invalid", ["tenant", "source", "hash", "deleted", "owner", "marker"])
async def test_success_state_does_not_override_invalid_document_evidence(isolated, monkeypatch, invalid):
    db, _ = isolated
    actors(db)
    create(db)
    await jobs.run_import_jobs_until_idle(limit=1)
    key = create(db)
    commit = Session.commit
    fired = False
    def lose_ack_with_invalid_evidence(session):
        nonlocal fired
        hit = not fired and any(isinstance(row, ImportJob) and row.id == key and row.status == "success"
                                for row in session.dirty)
        commit(session)
        if hit:
            fired = True
            with Session(db) as fresh:
                task = fresh.get(ImportJob, key)
                assert task.status == "success"
                doc = fresh.get(Document, task.document_id)
                if invalid == "tenant":
                    doc.tenant_id = "t-b"
                elif invalid == "source":
                    doc.source = "different.txt"
                elif invalid == "hash":
                    doc.content_hash = "different-content-hash"
                elif invalid == "deleted":
                    doc.deleted_at = datetime.now(UTC)
                elif invalid == "owner":
                    doc.user_id = "u-c"
                else:
                    fresh.add(Document(tenant_id="t-a", user_id="u-a", title="invalid marker",
                                       source="wrong.txt", source_kind="file", import_job_id=key))
                fresh.add(doc)
                commit(fresh)
            session.expire_all()
            raise OSError("synthetic acknowledgement with invalid persisted evidence")
    with monkeypatch.context() as fault:
        fault.setattr(Session, "commit", lose_ack_with_invalid_evidence)
        await jobs.run_import_jobs_once(limit=1)
    assert fired
    with Session(db) as session:
        task = session.get(ImportJob, key)
        assert task.status == "failed" and task.attempt_count == 1
    assert not jobs._registry().active
