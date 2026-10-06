"""清洗结构保真、原始快照与到期删除故障恢复。"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.rag import Document, DocumentIngestionSnapshot, VectorCleanupJob
from app.rag.cleaning import clean_document
from app.rag.document_parsers.base import ParsedBlock, ParsedDocument
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.retention import purge_expired_documents
from app.rag.service import RAGService


def parsed(text, blocks=None):
    return ParsedDocument(text, "test", "test.md", "md", "text/plain", blocks=blocks or [])


def test_versioned_structure_regressions():
    from pathlib import Path

    cases = json.loads((Path(__file__).parent / "fixtures/rag_cleaning_v1.json").read_text(encoding="utf-8"))
    for case in cases:
        result = clean_document(parsed(case["text"]))
        assert result.document.text == case["expected"], case["id"]
        assert clean_document(result.document).document.text == case["expected"], case["id"]


def test_cleaning_preserves_structure_and_is_idempotent():
    text = "\ufeff# 标题\r\n```\r\n  ┌────┐\r\n  │ a  │\r\n    code()\r\n```\r\n"
    original = parsed(text, [ParsedBlock("code", "  code()\r\n", 0)])
    result = clean_document(original)
    assert result.document.text == text.removeprefix("\ufeff").replace("\r\n", "\n")
    assert result.document.blocks[0].text == "  code()\n"
    assert original.text == text and original.blocks[0].text == "  code()\r\n"
    assert clean_document(result.document).document.text == result.document.text
    assert result.report["fallback"] is False


def test_bad_rule_falls_back_to_original(monkeypatch):
    monkeypatch.setattr("app.rag.cleaning._normalize", lambda text: text.strip())
    original = parsed("  code()\n", [ParsedBlock("code", "  code()", 0)])
    result = clean_document(original)
    assert result.document.text == original.text
    assert result.document.blocks == original.blocks
    assert result.report["fallback"] is True


def test_rule_exception_falls_back(monkeypatch):
    def fail(text):
        raise RuntimeError("private body")

    monkeypatch.setattr("app.rag.cleaning._normalize", fail)
    result = clean_document(parsed("  original"))
    assert result.document.text == "  original"
    assert result.report["error_code"] == "RuntimeError"


def test_sync_upload_keeps_raw_file_and_deduplicates(db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.core.database import get_session
    from app.main import app
    from app.models.user import User
    from app.rag.document_storage import read_source_file

    monkeypatch.setattr("app.rag.document_storage._PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("app.core.config.settings.EMBEDDING_PROVIDER", "mock")
    user = User(id="owner", tenant_id="upload-test", username="owner", hashed_password="", role="tenant_admin")

    def session_override():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = session_override
    raw = b"# title\r\n    code()\r\n"
    try:
        client = TestClient(app)
        first = client.post("/api/rag/documents/upload", files={"file": ("sample.md", raw, "text/markdown")})
        second = client.post("/api/rag/documents/upload", files={"file": ("sample.md", raw, "text/markdown")})
        assert first.status_code == second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        with Session(db) as session:
            doc = session.get(Document, first.json()["id"])
            assert read_source_file(doc.storage_path) == raw
            snapshot = session.get(DocumentIngestionSnapshot, doc.id)
            assert snapshot is not None and "    code()" in snapshot.original_text
        assert len(list((tmp_path / "data/knowledge").rglob("*.md"))) == 1
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def db(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "cleanup.db"))
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


async def test_cleanup_failure_survives_restart_and_succeeds(db, monkeypatch):
    now = datetime.now(UTC)
    store = AsyncMock()
    store.delete_by_document.side_effect = [RuntimeError("secret must not persist"), 1]
    monkeypatch.setattr("app.rag.retention.get_vector_store", lambda session: store)
    monkeypatch.setattr("app.core.config.settings.RAG_VECTOR_STORE", "local")
    audit = AsyncMock()
    monkeypatch.setattr("app.rag.retention.get_audit_logger", lambda: audit)
    with Session(db) as session:
        doc = Document(tenant_id="tenant", user_id="owner", title="expired", deleted_at=now - timedelta(days=91))
        session.add(doc)
        session.commit()
        doc_id = doc.id
        assert await purge_expired_documents(session, now=now) == 0
        assert session.get(Document, doc_id) is not None
        job = session.exec(select(VectorCleanupJob)).one()
        assert job.status == "pending" and job.attempt_count == 1
        assert job.last_error_code == "RuntimeError"
    with Session(db) as session:
        assert await purge_expired_documents(session, now=now + timedelta(seconds=61)) == 1
        assert session.get(Document, doc_id) is None
        job = session.exec(select(VectorCleanupJob)).one()
        assert job.status == "done" and job.attempt_count == 2
        assert await purge_expired_documents(session, now=now + timedelta(seconds=62)) == 0
    assert store.delete_by_document.await_count == 2
    store.delete_by_document.assert_awaited_with(doc_id, "tenant")


async def test_ingest_snapshots_cleaning_metadata_and_dedup(db):
    from app.models.rag import DocumentChunk

    with Session(db) as session:
        service = RAGService(session, "tenant", embedding_provider=MockEmbeddingProvider(dim=8))
        original = parsed("  缩进\r\n\ufffd正文")
        doc = await service.ingest_parsed_document(original, user_id="owner")
        snapshot = session.get(DocumentIngestionSnapshot, doc.id)
        assert snapshot.original_text == original.text
        report = json.loads(snapshot.cleaning_report)
        assert report["quality_score"] < 100 and report["version"] == "conservative-v1"
        assert report["original_hash"] != report["derived_hash"]
        chunks = session.exec(select(DocumentChunk).where(DocumentChunk.document_id == doc.id)).all()
        assert all(json.loads(c.chunk_metadata)["cleaning"]["version"] == "conservative-v1" for c in chunks)
        duplicate = await service.ingest_parsed_document(original, user_id="owner")
        assert duplicate.id == doc.id
        other = await service.ingest_parsed_document(original, user_id="peer")
        assert other.id != doc.id
        await service.reindex_document_in_place(doc, parsed("new"), content_hash="new-hash")
        session.refresh(snapshot)
        assert snapshot.original_text == "new"


async def test_cleanup_backoff_exhaustion_and_tenant_guard(db, monkeypatch):
    now = datetime.now(UTC)
    store = AsyncMock()
    store.delete_by_document.side_effect = RuntimeError("secret")
    monkeypatch.setattr("app.rag.retention.get_vector_store", lambda session: store)
    monkeypatch.setattr("app.core.config.settings.RAG_VECTOR_STORE", "local")
    monkeypatch.setattr("app.rag.retention.get_audit_logger", lambda: AsyncMock())
    with Session(db) as session:
        doc = Document(tenant_id="tenant", user_id="owner", title="expired", deleted_at=now - timedelta(days=91))
        session.add(doc)
        session.commit()
        doc_id = doc.id
        assert await purge_expired_documents(session, now=now) == 0
        assert await purge_expired_documents(session, now=now + timedelta(seconds=1)) == 0
        assert store.delete_by_document.await_count == 1
        assert await purge_expired_documents(session, now=now + timedelta(seconds=61)) == 0
        assert await purge_expired_documents(session, now=now + timedelta(seconds=182)) == 0
        job = session.exec(select(VectorCleanupJob)).one()
        assert job.status == "failed" and job.attempt_count == 3
        assert session.get(Document, doc_id) is not None
        assert await purge_expired_documents(session, now=now + timedelta(days=1)) == 0
        assert store.delete_by_document.await_count == 3
        job.status = "pending"
        job.next_attempt_at = now
        job.tenant_id = "other-tenant"
        session.add(job)
        session.commit()
        await purge_expired_documents(session, now=now + timedelta(days=1))
        assert job.last_error_code == "document_identity_mismatch"
        assert store.delete_by_document.await_count == 3


async def test_cleanup_target_drift_and_recent_document(db, monkeypatch):
    now = datetime.now(UTC)
    store = AsyncMock()
    store.delete_by_document.side_effect = RuntimeError()
    monkeypatch.setattr("app.rag.retention.get_vector_store", lambda session: store)
    monkeypatch.setattr("app.core.config.settings.RAG_VECTOR_STORE", "local")
    monkeypatch.setattr("app.rag.retention.get_audit_logger", lambda: AsyncMock())
    with Session(db) as session:
        recent = Document(tenant_id="tenant", user_id="owner", title="recent", deleted_at=now)
        expired = Document(tenant_id="tenant", user_id="owner", title="expired", deleted_at=now - timedelta(days=91))
        session.add(recent)
        session.add(expired)
        session.commit()
        await purge_expired_documents(session, now=now)
        job = session.exec(select(VectorCleanupJob)).one()
        job.vector_target = "another-target"
        session.add(job)
        session.commit()
        await purge_expired_documents(session, now=now + timedelta(seconds=61))
        assert job.status == "pending" and job.last_error_code == "vector_target_changed"
        assert store.delete_by_document.await_count == 1
        assert session.get(Document, recent.id) is not None


async def test_source_delete_failure_preserves_snapshot_until_retry(db, monkeypatch):
    now = datetime.now(UTC)
    store = AsyncMock()
    monkeypatch.setattr("app.rag.retention.get_vector_store", lambda session: store)
    monkeypatch.setattr("app.core.config.settings.RAG_VECTOR_STORE", "local")
    monkeypatch.setattr("app.rag.retention.get_audit_logger", lambda: AsyncMock())
    calls = []

    def delete(path):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("private path")
        return True

    monkeypatch.setattr("app.rag.retention.delete_source_file", delete)
    with Session(db) as session:
        doc = Document(
            tenant_id="tenant", user_id="owner", title="x", storage_path="test", deleted_at=now - timedelta(days=91)
        )
        session.add(doc)
        session.commit()
        doc_id = doc.id
        snapshot = DocumentIngestionSnapshot(
            document_id=doc_id, tenant_id="tenant", original_text="raw", original_blocks="[]", cleaning_report="{}"
        )
        session.add(snapshot)
        session.commit()
        assert await purge_expired_documents(session, now=now) == 0
        assert session.get(DocumentIngestionSnapshot, doc_id) is not None
        assert await purge_expired_documents(session, now=now + timedelta(seconds=61)) == 1
        assert session.get(DocumentIngestionSnapshot, doc_id) is None


def test_migration_upgrade_and_nonempty_downgrade_guard(tmp_path):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parents[1] / "alembic/versions/f9a014c6e001_add_rag_cleaning_and_cleanup_jobs.py"
    spec = importlib.util.spec_from_file_location("cleanup_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///" + str(tmp_path / "migration.db"))
    with engine.begin() as connection:
        Document.__table__.create(connection)
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
            migration.upgrade()
            connection.execute(
                DocumentIngestionSnapshot.__table__.insert().values(
                    document_id="id", tenant_id="t", original_text="raw", original_blocks="[]", cleaning_report="{}"
                )
            )
            with pytest.raises(RuntimeError, match="nonempty"):
                migration.downgrade()
            connection.execute(DocumentIngestionSnapshot.__table__.delete())
            migration.downgrade()
    engine.dispose()
