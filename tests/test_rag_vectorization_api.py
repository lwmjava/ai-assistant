"""API contract against a private in-memory database and offline embeddings."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.deps import get_current_user, get_db
from app.api.routes import rag as routes
from app.core.security import Role
from app.models.rag import Document, DocumentChunk
from app.models.user import User
from app.rag.embeddings.base import EmbeddingInputPolicy
from app.rag.embeddings.mock import MockEmbeddingProvider


@pytest.fixture
def api(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    user = User(
        id="api-user", tenant_id="api-tenant", username="fixture", hashed_password="", role=Role.TENANT_ADMIN.value
    )

    class RecordingProvider(MockEmbeddingProvider):
        def __init__(self):
            super().__init__(dim=4)
            self.input_policy = EmbeddingInputPolicy(40, counter=len, counting_method="test-characters")
            self.calls = []

        async def embed(self, texts):
            self.calls.append(list(texts))
            assert all(self.input_policy.check(text) is None for text in texts)
            return await super().embed(texts)

    provider = RecordingProvider()
    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: provider)
    monkeypatch.setattr("app.rag.document_storage._PROJECT_ROOT", tmp_path)

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "audit_event", no_audit)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: user

    def db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = db
    with TestClient(app) as client:
        yield client, engine, user, provider, app
    engine.dispose()


def test_http_ingest_detail_chunks_list_show_partial(api):
    client, engine, user, provider, app = api
    text = "before\n```\n" + "x" * 80 + "\n```\nafter"
    response = client.post("/api/rag/documents/ingest", json={"text": text, "title": "synthetic"})
    assert response.status_code == 200
    body = response.json()
    assert body["vectorization_status"] == "partial"
    assert body["vectorized_chunk_count"] == 2
    assert body["not_vectorized_chunk_count"] == 1
    assert body["embedding_skip_reason_counts"] == {"input_limit_exceeded": 1}
    assert all(len(value) <= 40 for call in provider.calls for value in call)
    document_id = body["id"]
    detail = client.get(f"/api/rag/documents/{document_id}").json()
    assert detail["vectorization_status"] == "partial"
    chunks = client.get(f"/api/rag/documents/{document_id}/chunks").json()
    skipped = [chunk for chunk in chunks if chunk["embedding_status"] == "not_vectorized"]
    assert skipped[0]["oversized"] is True
    assert skipped[0]["embedding_skip_reason"] == "input_limit_exceeded"
    assert "x" * 80 in skipped[0]["content"]
    assert client.get("/api/rag/documents").json()[0]["vectorization_status"] == "partial"


def test_legacy_invalid_metadata_and_safe_reason_allowlist(api):
    client, engine, user, provider, app = api
    with Session(engine) as session:
        doc = Document(id="legacy", tenant_id=user.tenant_id, user_id=user.id, title="legacy", chunk_count=4)
        session.add(doc)
        for index, (embedding, metadata) in enumerate(
            [
                ("[1,0]", "[]"),
                (None, "not-json"),
                (
                    None,
                    json.dumps(
                        {
                            "embedding_skip_reason": "input_limit_exceeded",
                            "oversized": True,
                            "embedding_input_policy": {"source": "https://private.invalid/secret"},
                        }
                    ),
                ),
                (None, json.dumps({"embedding_skip_reason": "raw-exception-with-secret"})),
            ]
        ):
            session.add(
                DocumentChunk(
                    document_id=doc.id,
                    tenant_id=user.tenant_id,
                    chunk_index=index,
                    content="legacy",
                    embedding=embedding,
                    chunk_metadata=metadata,
                )
            )
        session.commit()
    detail = client.get("/api/rag/documents/legacy").json()
    assert detail["vectorization_status"] == "unknown"
    assert detail["vectorized_chunk_count"] == 1
    assert detail["unknown_chunk_count"] == 1
    assert detail["not_vectorized_chunk_count"] == 2
    response = client.get("/api/rag/documents/legacy/chunks")
    assert response.status_code == 200
    assert response.json()[0]["embedding_status"] == "vectorized"
    assert response.json()[1]["embedding_status"] == "unknown"
    assert response.json()[3]["embedding_skip_reason"] == "unknown_reason"
    assert "private.invalid" not in response.text and "raw-exception" not in response.text


def test_cross_tenant_status_keeps_existing_admin_authority(api):
    client, engine, user, provider, app = api
    with Session(engine) as session:
        session.add(Document(id="foreign", tenant_id="other", user_id="other", title="foreign", chunk_count=1))
        session.add(
            DocumentChunk(document_id="foreign", tenant_id="other", content="secret", chunk_index=0, embedding="[1]")
        )
        session.commit()
    assert client.get("/api/rag/documents/foreign").status_code == 404
    assert client.get("/api/rag/documents/foreign/chunks").status_code == 404
    assert client.get("/api/rag/documents").json() == []
    user.role = Role.SYSTEM_ADMIN.value
    detail = client.get("/api/rag/documents/foreign").json()
    assert detail["vectorization_status"] == "vectorized"


def test_list_summary_uses_one_batch_query(api):
    client, engine, user, provider, app = api
    with Session(engine) as session:
        for index in range(5):
            doc = Document(id=f"batch-{index}", tenant_id=user.tenant_id, user_id=user.id, title="batch", chunk_count=1)
            session.add(doc)
            session.add(
                DocumentChunk(
                    document_id=doc.id, tenant_id=user.tenant_id, content="value", chunk_index=0, embedding="[1]"
                )
            )
        session.commit()
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "rag_document_chunks" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        response = client.get("/api/rag/documents")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200
    assert len(statements) == 1
    assert all(doc["vectorization_status"] == "vectorized" for doc in response.json())


def test_openapi_exposes_compatible_vectorization_fields(api):
    client, engine, user, provider, app = api
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert schemas["DocumentOut"]["properties"]["vectorization_status"]["default"] == "unknown"
    assert "embedding_skip_reason" in schemas["DocumentChunkOut"]["properties"]


def test_upload_and_publish_report_saved_but_not_vectorized(api):
    client, engine, user, provider, app = api
    text = "```\n" + "x" * 80 + "\n```\n"
    response = client.post("/api/rag/documents/upload", files={"file": ("synthetic.md", text, "text/markdown")})
    assert response.status_code == 200
    body = response.json()
    assert body["vectorization_status"] == "not_vectorized"
    assert body["not_vectorized_chunk_count"] == 1
    assert provider.calls == []
    published = client.post(f"/api/rag/documents/{body['id']}/publish")
    assert published.status_code == 200
    assert published.json()["vectorization_status"] == "not_vectorized"
    reparse = client.post(f"/api/rag/documents/{body['id']}/reparse")
    assert reparse.status_code == 202
    assert reparse.json()["status"] == "pending"
    assert "vectorization_status" not in reparse.json()
    assert client.get(f"/api/rag/documents/{body['id']}").json()["vectorization_status"] == "not_vectorized"


def test_no_chunks_unknown_and_corrupt_cross_tenant_chunk_is_excluded(api):
    client, engine, user, provider, app = api
    with Session(engine) as session:
        session.add(Document(id="empty", tenant_id=user.tenant_id, user_id=user.id, title="empty"))
        session.add(
            DocumentChunk(document_id="empty", tenant_id="other", content="corrupt", chunk_index=0, embedding="[1]")
        )
        session.commit()
    body = client.get("/api/rag/documents/empty").json()
    assert body["vectorization_status"] == "unknown"
    assert body["vectorized_chunk_count"] == 0


def test_legacy_explicit_skipped_without_reason_is_not_vectorized(api):
    client, engine, user, provider, app = api
    with Session(engine) as session:
        session.add(Document(id="skipped", tenant_id=user.tenant_id, user_id=user.id, title="legacy", chunk_count=1))
        session.add(
            DocumentChunk(
                document_id="skipped",
                tenant_id=user.tenant_id,
                content="legacy",
                chunk_index=0,
                chunk_metadata=json.dumps({"embedding_status": "not_vectorized"}),
            )
        )
        session.commit()
    body = client.get("/api/rag/documents/skipped").json()
    assert body["vectorization_status"] == "not_vectorized"
    assert body["embedding_skip_reason_counts"] == {"unknown_reason": 1}


@pytest.mark.parametrize("declared_count,expected_unknown", [(2, 1), (0, 0), (-1, 0)])
def test_inconsistent_chunk_count_cannot_claim_complete(api, declared_count, expected_unknown):
    client, engine, user, provider, app = api
    with Session(engine) as session:
        session.add(
            Document(
                id="mismatch", tenant_id=user.tenant_id, user_id=user.id, title="mismatch", chunk_count=declared_count
            )
        )
        session.add(DocumentChunk(document_id="mismatch", tenant_id=user.tenant_id, content="known", embedding="[1]"))
        session.commit()
    body = client.get("/api/rag/documents/mismatch").json()
    assert body["vectorization_status"] == "unknown"
    assert body["vectorized_chunk_count"] == 1
    assert body["unknown_chunk_count"] == expected_unknown


def test_large_document_list_summary_uses_bounded_batches(api):
    client, engine, user, provider, app = api
    with Session(engine) as session:
        for index in range(201):
            session.add(Document(id=f"many-{index}", tenant_id=user.tenant_id, user_id=user.id, title="empty"))
        session.commit()
    parameter_counts = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "rag_document_chunks" in statement:
            parameter_counts.append(len(parameters))

    event.listen(engine, "before_cursor_execute", record)
    try:
        response = client.get("/api/rag/documents")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200
    assert len(response.json()) == 201
    assert len(parameter_counts) == 2
    assert max(parameter_counts) <= 401  # 400 ID/tenant values plus the empty-vector comparison.


def test_chunks_do_not_expose_corrupt_foreign_tenant_association(api):
    client, engine, user, provider, app = api
    with Session(engine) as session:
        session.add(
            Document(id="owned-corrupt", tenant_id=user.tenant_id, user_id=user.id, title="owned", chunk_count=2)
        )
        session.add(
            DocumentChunk(
                document_id="owned-corrupt",
                tenant_id=user.tenant_id,
                content="authorized-body",
                chunk_index=0,
                embedding="[1]",
            )
        )
        session.add(
            DocumentChunk(
                document_id="owned-corrupt",
                tenant_id="foreign",
                content="private-body-marker",
                chunk_index=1,
                chunk_metadata=json.dumps(
                    {"embedding_status": "not_vectorized", "embedding_skip_reason": "private-reason-marker"}
                ),
            )
        )
        session.commit()
    response = client.get("/api/rag/documents/owned-corrupt/chunks")
    assert response.status_code == 200
    assert [chunk["content"] for chunk in response.json()] == ["authorized-body"]
    assert "private-body-marker" not in response.text and "private-reason-marker" not in response.text
    summary = client.get("/api/rag/documents/owned-corrupt").json()
    assert summary["vectorization_status"] == "unknown"
    assert summary["vectorized_chunk_count"] == 1
    assert summary["unknown_chunk_count"] == 1
    assert summary["embedding_skip_reason_counts"] == {}
