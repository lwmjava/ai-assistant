"""Retrieval cache acceptance: isolated SQL/local vector path, synthetic embeddings."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.models.user import Tenant, User
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.service import RAGService
from app.rag.vectorstore.local import LocalVectorStore


async def test_cache_repeated_query_avoids_local_vector_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "RETRIEVAL_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_MIN_SIMILARITY", 0.0)
    engine = create_engine(f"sqlite:///{(tmp_path / 'cache.db').as_posix()}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        tenant = Tenant(id="cache-tenant", name="Cache test")
        reader = User(id="cache-reader", tenant_id=tenant.id, username="cache-reader", hashed_password="unused")
        session.add(tenant)
        session.add(reader)
        session.commit()
        store = LocalVectorStore(session)
        original = store.hybrid_search
        calls = 0

        async def counting(*args, **kwargs):
            nonlocal calls
            calls += 1
            return await original(*args, **kwargs)

        monkeypatch.setattr(store, "hybrid_search", counting)
        rag = RAGService(
            session, tenant.id, embedding_provider=MockEmbeddingProvider(dim=64), vector_store=store, reader=reader
        )
        await rag.ingest_text("Cache repeated keyword example", title="Cache", source="cache.txt", user_id=reader.id)
        first = await rag.search("keyword", top_k=2)
        second = await rag.search("keyword", top_k=2)
        assert first and second
        assert calls == 1
        second[0].content = "corrupted copy"
        assert (await rag.search("keyword", top_k=2))[0].content != "corrupted copy"
        retriever = rag.make_retriever(top_k=2)
        assert await retriever.retrieve("keyword", "")
        assert calls == 1  # Dialog retriever shares the authorized retrieval cache.
        await rag.search("keyword", top_k=3)
        assert calls == 2
        monkeypatch.setattr(settings, "RETRIEVAL_CACHE_ENABLED", False)
        await rag.search("keyword", top_k=2)
        await rag.search("keyword", top_k=2)
        assert calls == 4


async def test_cache_expiry_date_crossing_invalidates_without_database_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "RETRIEVAL_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_EFFECTIVE_DATE_FILTER", True)
    now = datetime(2026, 10, 11, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr("app.rag.effective_date.datetime", Clock)
    engine = create_engine(f"sqlite:///{(tmp_path / 'date-cache.db').as_posix()}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        tenant = Tenant(id="date-tenant", name="Date cache")
        reader = User(id="date-reader", tenant_id=tenant.id, username="date-reader", hashed_password="unused")
        session.add(tenant)
        session.add(reader)
        session.commit()
        rag = RAGService(session, tenant.id, embedding_provider=MockEmbeddingProvider(dim=64), reader=reader)
        doc = await rag.ingest_text("expiring keyword content", title="Date", source="date.txt", user_id=reader.id)
        doc.expires_at = now + timedelta(seconds=10)
        session.add(doc)
        session.commit()
        assert await rag.search("keyword")
        assert await rag.search("keyword")
        now += timedelta(seconds=11)
        assert await rag.search("keyword") == []
