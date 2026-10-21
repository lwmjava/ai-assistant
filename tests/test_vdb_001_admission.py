"""默认向量库切换前的领域边界。

覆盖远端不可用、部分重建、调用方数据库绑定、外租户候选回查，
以及嵌入响应模型与非有限数值。
"""

from __future__ import annotations

import json
import math
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core import database
from app.core.config import settings
from app.models.rag import Document, DocumentChunk
from app.rag.access import ReadScope
from app.rag.index_identity import EmbeddingIndexIdentity, IndexIdentityError
from app.rag.index_registry import activate_index, active_index, ensure_index
from app.rag.vectorstore.milvus import MilvusVectorStore


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + (tmp_path / "actual.db").as_posix())
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _identity(model: str, backend: str) -> EmbeddingIndexIdentity:
    return EmbeddingIndexIdentity(
        backend=backend, provider="mock", model=model, dim=2, index_version="1"
    )


def _add_chunk(db: Session, target, doc: Document | None = None, *, embedding: list[float] | None = None):
    if doc is None:
        doc = Document(tenant_id="synthetic", user_id="owner", title="synthetic", is_current=True)
        db.add(doc)
        db.flush()
    row = DocumentChunk(
        document_id=doc.id,
        tenant_id=doc.tenant_id,
        content="synthetic",
        embedding=json.dumps(embedding if embedding is not None else [1.0, 0.0]),
        tokens="[]",
        index_id=target.id,
    )
    db.add(row)
    db.commit()
    return doc


def _prepare(db: Session, backend: str):
    old = ensure_index(db, _identity("old", backend))
    activate_index(db, old.id, allow_empty=True)
    target = ensure_index(db, _identity("new", backend))
    db.commit()
    return old, target


def test_remote_unavailable_cannot_pass_activation_gate(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    old, target = _prepare(db, "milvus")
    _add_chunk(db, target)

    def disconnected(*args, **kwargs):
        raise RuntimeError("synthetic Milvus unavailable")

    monkeypatch.setattr(MilvusVectorStore, "_connect", disconnected)
    with pytest.raises((IndexIdentityError, RuntimeError)):
        activate_index(db, target.id, expected_identity=_identity("new", "milvus"))
    assert active_index(db, "milvus").id == old.id


def test_incomplete_rebuild_cannot_retire_full_old_index(db: Session) -> None:
    old, target = _prepare(db, "local")
    doc_one = _add_chunk(db, old)
    _add_chunk(db, old)
    _add_chunk(db, target, doc_one)
    registered = target.chunk_count
    with pytest.raises(IndexIdentityError):
        activate_index(db, target.id, expected_identity=_identity("new", "local"))
    assert active_index(db, "local").id == old.id
    assert target.chunk_count == registered


def test_activation_uses_supplied_session_database(db: Session, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, target = _prepare(db, "local")
    _add_chunk(db, target)
    unrelated = create_engine("sqlite:///" + (tmp_path / (uuid4().hex + ".db")).as_posix())
    SQLModel.metadata.create_all(unrelated)
    monkeypatch.setattr(database, "engine", unrelated)
    try:
        activate_index(db, target.id, expected_identity=_identity("new", "local"))
        assert active_index(db, "local").id == target.id
    finally:
        unrelated.dispose()


def test_zero_vector_cannot_activate_index(db: Session) -> None:
    old, target = _prepare(db, "local")
    _add_chunk(db, target, embedding=[0.0, 0.0])
    with pytest.raises(IndexIdentityError):
        activate_index(db, target.id, expected_identity=_identity("new", "local"))
    assert active_index(db, "local").id == old.id


def test_non_finite_vector_cannot_activate_index(db: Session) -> None:
    old, target = _prepare(db, "local")
    _add_chunk(db, target, embedding=[math.nan, 0.0])
    with pytest.raises(IndexIdentityError):
        activate_index(db, target.id, expected_identity=_identity("new", "local"))
    assert active_index(db, "local").id == old.id


@pytest.mark.asyncio
async def test_sql_recheck_rejects_foreign_tenant_id_from_remote(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RAG_EFFECTIVE_DATE_FILTER", False)
    caller = "caller-" + uuid4().hex
    victim = "victim-" + uuid4().hex
    identity = EmbeddingIndexIdentity(
        "milvus", "synthetic", "boundary-probe", 2, index_version=uuid4().hex
    )
    index = ensure_index(db, identity)
    index.status = "active"
    document = Document(tenant_id=victim, user_id="victim-owner", title="foreign document")
    db.add(document)
    db.flush()
    chunk = DocumentChunk(
        tenant_id=victim,
        document_id=document.id,
        content="FOREIGN_EVIDENCE",
        embedding="[1.0, 0.0]",
        tokens="[]",
        index_id=index.id,
    )
    db.add(chunk)
    db.commit()
    store = MilvusVectorStore(db)
    monkeypatch.setattr(
        store,
        "_remote_search",
        lambda *args: [SimpleNamespace(entity={"id": chunk.id}, distance=1.0)],
    )
    results = await store.hybrid_search(
        [1.0, 0.0], [], caller, 2, read_scope=ReadScope(caller), identity=identity
    )
    assert results == []


@pytest.mark.asyncio
async def test_sql_recheck_rejects_document_tenant_mismatch(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RAG_EFFECTIVE_DATE_FILTER", False)
    caller = "caller-" + uuid4().hex
    identity = EmbeddingIndexIdentity(
        "milvus", "synthetic", "boundary-owner", 2, index_version=uuid4().hex
    )
    index = ensure_index(db, identity)
    index.status = "active"
    document = Document(tenant_id="other-tenant", user_id="owner", title="mismatched")
    db.add(document)
    db.flush()
    chunk = DocumentChunk(
        tenant_id=caller,
        document_id=document.id,
        content="MISMATCHED_OWNER",
        embedding="[1.0, 0.0]",
        tokens="[]",
        index_id=index.id,
    )
    db.add(chunk)
    db.commit()
    store = MilvusVectorStore(db)
    monkeypatch.setattr(
        store,
        "_remote_search",
        lambda *args: [SimpleNamespace(entity={"id": chunk.id}, distance=1.0)],
    )
    results = await store.hybrid_search(
        [1.0, 0.0], [], caller, 2, read_scope=ReadScope(caller), identity=identity
    )
    assert results == []


def test_real_embedding_rejects_unexpected_response_model(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from scripts.rag_hybrid_embedding_check import Budget, embed

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                headers={},
                json=lambda: {
                    "model": "unapproved-model",
                    "usage": {"prompt_tokens": 44},
                    "data": [{"index": i, "embedding": [1.0] + [0.0] * 1023} for i in range(4)],
                },
            )

    monkeypatch.setattr(httpx, "Client", Client)
    config = SimpleNamespace(
        EMBEDDING_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
        EMBEDDING_MODEL="text-embedding-v3",
        EMBEDDING_DIM=1024,
        EMBEDDING_API_KEY="synthetic-placeholder",
    )
    budget = Budget(tmp_path / "isolated-budget.sqlite")
    try:
        with pytest.raises(ValueError):
            embed(config, budget)
    finally:
        budget.db.close()


@pytest.mark.asyncio
async def test_provider_embed_rejects_unexpected_response_model(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.rag.embeddings.base import EmbeddingInputPolicy
    from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider

    class Response:
        is_error = False
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "model": "unapproved-model",
                "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0]}],
            }

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://embed.example.com/v1",
        api_key="synthetic-placeholder",
        model="text-embedding-v3",
        dim=4,
        input_policy=EmbeddingInputPolicy(
            max_input_tokens=8192, counter=len, counting_method="synthetic-test", source="fixture"
        ),
    )
    with pytest.raises(ValueError, match="unapproved-model"):
        await provider.embed(["synthetic"])


@pytest.mark.asyncio
async def test_provider_embed_rejects_non_finite_values(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.rag.embeddings.base import EmbeddingInputPolicy
    from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider

    class Response:
        is_error = False
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"model": "text-embedding-v3", "data": [{"index": 0, "embedding": [math.nan, 0.0, 0.0, 0.0]}]}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://embed.example.com/v1",
        api_key="synthetic-placeholder",
        model="text-embedding-v3",
        dim=4,
        input_policy=EmbeddingInputPolicy(
            max_input_tokens=8192, counter=len, counting_method="synthetic-test", source="fixture"
        ),
    )
    with pytest.raises(ValueError, match="invalid values"):
        await provider.embed(["synthetic"])
