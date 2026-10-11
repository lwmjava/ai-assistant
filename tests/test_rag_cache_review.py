"""Independent domain counterexamples for transaction-visible cache behavior."""

import pytest
from sqlalchemy import update
from sqlmodel import Session

from app.models.rag import Document
from app.models.user import User
from app.rag.backend.native import NativeRagBackend
from app.rag.embeddings.mock import tokenize
from app.rag.service import RAGService
from tests.test_rag_019_acceptance import (  # noqa: F401 -- fixture registration
    CountingLocalStore,
    SyntheticEmbedding,
    rag_for,
)
from tests.test_rag_019_acceptance import (
    cache_env as _cache_env,
)

cache_env = _cache_env


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    "orm_flush", "session_dml", "connection_dml", "nested_rollback", "sqlite_driver_dml",
])
async def test_cache_review_flushed_publication_bypasses_committed_negative_cache(cache_env, mutation):
    with Session(cache_env) as session:
        rag, store, user = rag_for(session)
        doc = await rag.ingest_text(
            "transaction-visible publication keyword", "draft", "synthetic", user.id,
            is_current=False, version_state="draft",
        )
        assert await rag.search("publication") == []
        assert await rag.search("publication") == []
        assert store.calls == 1

        if mutation in {"orm_flush", "nested_rollback"}:
            doc.is_current = True
            doc.version_state = "published"
            session.add(doc)
            session.flush()
            if mutation == "nested_rollback":
                nested = session.begin_nested()
                nested.rollback()
        elif mutation == "sqlite_driver_dml":
            session.connection().connection.driver_connection.execute(
                "UPDATE rag_documents SET is_current = 1, version_state = 'published' WHERE id = ?",
                (doc.id,),
            )
        else:
            command = update(Document).where(Document.id == doc.id).values(
                is_current=True, version_state="published"
            )
            if mutation == "session_dml":
                session.execute(command)
            else:
                session.connection().execute(command)
        assert not session.new and not session.dirty and not session.deleted

        result = await rag.search("publication")
        assert result and result[0].document_id == doc.id
        assert store.calls == 2, "flushed but uncommitted writes must bypass old committed cache"
        session.rollback()

        assert await rag.search("publication") == []
        assert store.calls == 2, "outer rollback restores the earlier committed cache eligibility"


@pytest.mark.asyncio
async def test_cache_review_opaque_backend_mutation_must_not_replay_old_hits(cache_env):
    class MutableBackend(NativeRagBackend):
        enabled = True

        async def retrieve(self, query, *, tenant_id, top_k, read_scope=None):
            if not self.enabled:
                return []
            return await super().retrieve(query, tenant_id=tenant_id, top_k=top_k, read_scope=read_scope)

    with Session(cache_env) as session:
        user = session.get(User, "alice")
        embedding = SyntheticEmbedding()
        store = CountingLocalStore(session)
        backend = MutableBackend(embedding, store, tokenize)
        rag = RAGService(session, user.tenant_id, embedding, store, reader=user, backend=backend)
        await rag.ingest_text("opaque backend keyword", "custom", "synthetic", user.id)
        assert await rag.search("keyword")
        backend.enabled = False
        assert await rag.search("keyword") == []


@pytest.mark.asyncio
async def test_cache_review_actual_backend_embedding_mismatch_bypasses_nominal_key(cache_env):
    class MutableEmbedding(SyntheticEmbedding):
        zero = False

        async def embed(self, texts):
            return [[0.0, 0.0, 0.0] for _ in texts] if self.zero else await super().embed(texts)

    with Session(cache_env) as session:
        user = session.get(User, "alice")
        nominal = SyntheticEmbedding()
        actual = MutableEmbedding()
        store = CountingLocalStore(session)
        backend = NativeRagBackend(actual, store, tokenize)
        rag = RAGService(session, user.tenant_id, nominal, store, reader=user, backend=backend)
        await rag.ingest_text("dependency identity mismatch", "binding", "synthetic", user.id)
        first = await rag.search("unrelatedzeroquery")
        assert first and first[0].similarity > 0
        actual.zero = True
        changed = await rag.search("unrelatedzeroquery")
        assert store.calls == 2
        assert changed and changed[0].similarity == 0

