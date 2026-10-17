"""Remote rebuild regression cases use synthetic vectors and isolated SQL stores."""
import asyncio
import json
import re
from collections.abc import Sequence

import pytest
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.core.config import settings
from app.models.rag import DocumentChunk, EmbeddingIndex
from app.rag.document_parsers.base import ParsedDocument
from app.rag.embeddings.base import EmbeddingInputPolicy, EmbeddingProvider
from app.rag.import_trace import ImportTraceError
from app.rag.index_identity import IndexIdentityError, identity_from_provider
from app.rag.index_registry import activate_index, active_index, ensure_index
from app.rag.service import RAGService
from app.rag.vectorstore.milvus import MilvusVectorStore


class SyntheticProvider(EmbeddingProvider):
    def __init__(self, model: str):
        self.model = model
        self.dim = 8
        self.input_policy = EmbeddingInputPolicy(
            max_input_tokens=None, counting_method="offline-unlimited", source="synthetic-regression"
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.1] * self.dim for _ in texts]


class RecordingMilvus(MilvusVectorStore):
    """Use real add/delete routing with an in-memory collection boundary."""
    def __init__(self, session):
        super().__init__(session)
        self.collections = {}
        self.connections = []

    def _connect(self, identity=None, index=None):
        target = index if index is not None else self._resolve_index(identity)
        self.connections.append(target.id)
        return self.collections.setdefault(target.id, Collection())


class Collection:
    def __init__(self):
        self.rows = {}
        self.fail_write = False
        self.fail_delete = False

    def upsert(self, entities):
        for entity in entities:
            self.rows[entity["id"]] = entity
            if self.fail_write:
                raise RuntimeError("synthetic partial write")

    def query(self, expr, output_fields, **kwargs):
        filters = re.findall(r'(document_id|tenant_id|index_id) == "([^"]+)"', expr)
        return [{"id": row["id"]} for row in self.rows.values()
                if all(row[key] == value for key, value in filters)]

    def delete(self, expr):
        if self.fail_delete:
            raise RuntimeError("synthetic delete failure")
        for row in self.query(expr, ["id"]):
            del self.rows[row["id"]]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "milvus")
    monkeypatch.setattr(settings, "RAG_BACKEND", "native")
    engine = create_engine("sqlite:///" + (tmp_path / "isolated.db").as_posix())
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


async def setup_rebuild(session):
    store = RecordingMilvus(session)
    old_provider = SyntheticProvider("synthetic-old")
    old_service = RAGService(session, "synthetic-tenant", embedding_provider=old_provider, vector_store=store)
    doc = await old_service.ingest_text("Synthetic evidence.", "synthetic", "synthetic.txt", "user")
    old_id = active_index(session, "milvus").id
    old_remote_ids = set(store.collections[old_id].rows)
    new_provider = SyntheticProvider("synthetic-new")
    target = ensure_index(session, identity_from_provider(new_provider))
    session.commit()  # CLI prepare persists the target before any remote side effect.
    service = RAGService(session, "synthetic-tenant", embedding_provider=new_provider,
                         vector_store=store, write_index_id=target.id)
    parsed = ParsedDocument(text="Synthetic evidence.", title="synthetic", source="synthetic.txt",
                            extension=".txt", content_type=None)
    return store, doc, old_id, old_remote_ids, target, service, parsed


@pytest.mark.asyncio
async def test_preparing_rebuild_preserves_old_remote_vectors(isolated):
    store, doc, old_id, old_remote_ids, target, service, parsed = await setup_rebuild(isolated)
    await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    assert set(store.collections[old_id].rows) == old_remote_ids
    assert active_index(isolated, "milvus").id == old_id


@pytest.mark.asyncio
async def test_preparing_rebuild_writes_target_vectors(isolated):
    store, doc, old_id, old_remote_ids, target, service, parsed = await setup_rebuild(isolated)
    await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    rows = isolated.exec(select(DocumentChunk).where(col(DocumentChunk.index_id) == target.id)).all()
    assert rows
    assert target.id in store.collections, "new vectors must be written to target collection"
    assert set(store.collections[target.id].rows) == {row.id for row in rows if row.embedding}
    assert all(row["index_id"] == target.id for row in store.collections[target.id].rows.values())


@pytest.mark.asyncio
async def test_repeat_rebuild_replaces_only_target_same_document(isolated):
    store, doc, old_id, old_ids, target, service, parsed = await setup_rebuild(isolated)
    await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    first_ids = set(store.collections[target.id].rows)
    await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    target_rows = isolated.exec(select(DocumentChunk).where(col(DocumentChunk.index_id) == target.id)).all()
    assert len(target_rows) == len(first_ids) == 1
    assert set(store.collections[target.id].rows) == {row.id for row in target_rows}
    assert not first_ids & set(store.collections[target.id].rows)
    assert set(store.collections[old_id].rows) == old_ids


@pytest.mark.asyncio
async def test_repeat_rebuild_preserves_other_documents_in_target(isolated):
    store, doc, old_id, old_ids, target, service, parsed = await setup_rebuild(isolated)
    old_service = RAGService(isolated, "synthetic-tenant", embedding_provider=SyntheticProvider("synthetic-old"),
                             vector_store=store)
    other = await old_service.ingest_text("Other synthetic evidence.", "other", "other.txt", "user")
    other_service = RAGService(isolated, "synthetic-tenant", embedding_provider=SyntheticProvider("synthetic-new"),
                               vector_store=store, write_index_id=target.id)
    other_parsed = ParsedDocument(text="Other synthetic evidence.", title="other", source="other.txt",
                                  extension=".txt", content_type=None)
    await other_service.reindex_document_in_place(other, other_parsed, content_hash=other.content_hash or "")
    other_ids = set(store.collections[target.id].rows)
    await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    assert other_ids <= set(store.collections[target.id].rows)
    assert len(store.collections[target.id].rows) == 2


@pytest.mark.asyncio
async def test_unknown_remote_store_refuses_preparing_before_deleting(isolated):
    from app.rag.vectorstore.base import VectorStore
    store, doc, old_id, old_ids, target, service, parsed = await setup_rebuild(isolated)
    class UnknownStore(VectorStore):
        async def add(self, chunks):
            raise AssertionError("unknown remote add must not be called")
        async def delete_by_document(self, document_id, tenant_id):
            raise AssertionError("active cleanup must not be called")
        async def hybrid_search(self, *args, **kwargs):
            return []
        async def count(self, tenant_id):
            return 0
    service._vector_store = UnknownStore()
    with pytest.raises(IndexIdentityError, match="未声明"):
        await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    assert active_index(isolated, "milvus").id == old_id
    assert set(store.collections[old_id].rows) == old_ids


@pytest.mark.asyncio
async def test_target_delete_refuses_wrong_identity_before_connect(isolated):
    store, doc, old_id, old_ids, target, service, parsed = await setup_rebuild(isolated)
    before = len(store.connections)
    with pytest.raises(IndexIdentityError):
        await store.delete_by_document(doc.id, doc.tenant_id,
            target_index_id=target.id, identity=identity_from_provider(SyntheticProvider("synthetic-old")))
    assert len(store.connections) == before
    assert set(store.collections[old_id].rows) == old_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["partial_write", "sql_commit", "target_delete"])
async def test_failure_evidence_survives_rollback_and_old_index_is_preserved(isolated, monkeypatch, fault):
    store, doc, old_id, old_ids, target, service, parsed = await setup_rebuild(isolated)
    target_id = target.id
    if fault == "target_delete":
        await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
        store.collections[target_id].fail_delete = True
    elif fault == "partial_write":
        store.collections[target_id] = Collection()
        store.collections[target_id].fail_write = True
    else:
        def fail_commit():
            raise RuntimeError("synthetic SQL commit failure")
        monkeypatch.setattr(isolated, "commit", fail_commit)
    with pytest.raises((RuntimeError, ImportTraceError)):
        await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    with Session(isolated.get_bind()) as fresh:
        failed = fresh.get(EmbeddingIndex, target_id)
        assert failed.status == "failed"
        detail = json.loads(failed.notes)
        assert detail["target_index_id"] == target_id
        assert detail["compensation_required"] is True
        if fault != "target_delete":
            assert detail["new_chunk_ids"]
            assert set(store.collections[target_id].rows) <= set(detail["new_chunk_ids"])
            assert not fresh.exec(select(DocumentChunk).where(col(DocumentChunk.index_id) == target_id)).all()
        assert active_index(fresh, "milvus").id == old_id
        with pytest.raises(IndexIdentityError):
            activate_index(fresh, target_id)
    assert set(store.collections[old_id].rows) == old_ids


@pytest.mark.asyncio
async def test_retry_cleans_only_failed_target_residue(isolated):
    store, doc, old_id, old_ids, target, service, parsed = await setup_rebuild(isolated)
    target_id = target.id
    store.collections[target_id] = Collection()
    store.collections[target_id].fail_write = True
    with pytest.raises(RuntimeError):
        await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    residue = set(store.collections[target_id].rows)
    assert residue
    isolated.refresh(target)
    assert target.status == "failed"
    target.status = "preparing"  # explicit prepare, preserving compensation notes
    isolated.add(target)
    isolated.commit()
    store.collections[target_id].fail_write = False
    await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    rows = isolated.exec(select(DocumentChunk).where(col(DocumentChunk.index_id) == target_id)).all()
    assert set(store.collections[target_id].rows) == {row.id for row in rows}
    assert not residue & set(store.collections[target_id].rows)
    assert set(store.collections[old_id].rows) == old_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("violation", ["identity", "chunk_index", "tenant", "dim", "failed_target"])
async def test_target_batch_is_validated_before_any_remote_connection(isolated, violation):
    store, doc, old_id, old_ids, target, service, parsed = await setup_rebuild(isolated)
    identity = identity_from_provider(SyntheticProvider("synthetic-new"))
    chunks = [DocumentChunk(id="good", tenant_id=doc.tenant_id, document_id=doc.id,
        content="synthetic", index_id=target.id, embedding=json.dumps([.1] * 8)),
        DocumentChunk(id="bad", tenant_id=doc.tenant_id, document_id=doc.id,
        content="synthetic", index_id=target.id, embedding=json.dumps([.1] * 8))]
    if violation == "identity":
        identity = identity_from_provider(SyntheticProvider("synthetic-old"))
    elif violation == "chunk_index":
        chunks[-1].index_id = old_id
    elif violation == "tenant":
        chunks[-1].tenant_id = "other-tenant"
    elif violation == "dim":
        chunks[-1].embedding = json.dumps([.1] * 16)
    else:
        target.status = "failed"
    calls_before = len(store.connections)
    with pytest.raises(IndexIdentityError):
        await store.add(chunks, identity, target_index_id=target.id)
    assert len(store.connections) == calls_before
    assert set(store.collections[old_id].rows) == old_ids


@pytest.mark.asyncio
async def test_invalid_rebuild_preserves_existing_target_before_cleanup(isolated, monkeypatch):
    store, doc, old_id, old_ids, target, service, parsed = await setup_rebuild(isolated)
    await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    before = set(store.collections[target.id].rows)
    async def invalid(texts):
        return [[.1] * 16 for _ in texts]
    monkeypatch.setattr(service._embedding, "embed", invalid)
    with pytest.raises(IndexIdentityError):
        await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    assert set(store.collections[target.id].rows) == before
    assert set(store.collections[old_id].rows) == old_ids


def test_cli_rebuild_rejects_active_target_without_disabling_it(isolated, monkeypatch):
    import sys

    from scripts import rebuild_embedding_index as cli
    _, _, old_id, _, _, _, _ = asyncio.run(setup_rebuild(isolated))
    monkeypatch.setattr(cli, "engine", isolated.get_bind())
    monkeypatch.setattr(cli, "init_db", lambda: None)
    from app.rag.embeddings import factory
    monkeypatch.setattr(factory, "get_embedding_provider", lambda: SyntheticProvider("synthetic-old"))
    monkeypatch.setattr(sys, "argv", ["rebuild_embedding_index.py", "rebuild", "--apply"])
    assert cli.main() == 3
    with Session(isolated.get_bind()) as fresh:
        assert active_index(fresh, "milvus").id == old_id


@pytest.mark.asyncio
async def test_cancelled_remote_write_records_compensation_and_reraises(isolated, monkeypatch):
    store, doc, old_id, old_ids, target, service, parsed = await setup_rebuild(isolated)
    target_id = target.id
    original = store.add
    async def cancel(chunks, identity=None, **kwargs):
        await original(chunks[:1], identity, **kwargs)
        raise asyncio.CancelledError()
    monkeypatch.setattr(store, "add", cancel)
    with pytest.raises(asyncio.CancelledError):
        await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    isolated.rollback()
    with Session(isolated.get_bind()) as fresh:
        failed = fresh.get(EmbeddingIndex, target_id)
        assert failed.status == "failed"
        detail = json.loads(failed.notes)
        assert detail["exception_type"] == "CancelledError"
        assert detail["compensation_required"] is True
        assert set(store.collections[target_id].rows) <= set(detail["new_chunk_ids"])
        assert active_index(fresh, "milvus").id == old_id
    assert set(store.collections[old_id].rows) == old_ids


@pytest.mark.asyncio
async def test_target_cleanup_uses_strong_visibility_for_recent_upsert(isolated, monkeypatch):
    store, doc, old_id, old_ids, target, service, parsed = await setup_rebuild(isolated)
    await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    collection = store.collections[target.id]
    query = collection.query
    observations = []
    def visibility(expr, output_fields, **kwargs):
        observations.append(kwargs.get("consistency_level"))
        if kwargs.get("consistency_level") != "Strong":
            return []
        return query(expr, output_fields)
    monkeypatch.setattr(collection, "query", visibility)
    # delete's fake implementation calls query itself; the real server executes expr directly.
    def delete(expr):
        for row in query(expr, ["id"]):
            del collection.rows[row["id"]]
    monkeypatch.setattr(collection, "delete", delete)
    await service.reindex_document_in_place(doc, parsed, content_hash=doc.content_hash or "")
    assert observations == ["Strong"]
    rows = isolated.exec(select(DocumentChunk).where(col(DocumentChunk.index_id) == target.id)).all()
    assert set(collection.rows) == {row.id for row in rows}
    assert set(store.collections[old_id].rows) == old_ids
