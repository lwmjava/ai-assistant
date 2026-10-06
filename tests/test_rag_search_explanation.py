"""Search explanation contract: identity, relationship and retrieval provenance."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.models.rag import Document, DocumentChunk
from app.rag.service import RAGService
from app.rag.vectorstore.base import ChunkResult


@pytest.fixture()
def service(monkeypatch):
    monkeypatch.setattr(settings, "RAG_EFFECTIVE_DATE_FILTER", False)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Document(id="doc", tenant_id="tenant", user_id="user", title="test"))
        session.add(DocumentChunk(
            id="parent", tenant_id="tenant", document_id="doc", chunk_index=0,
            content="full text", source="test", chunk_metadata='{"kind":"parent"}',
        ))
        session.add(DocumentChunk(
            id="child", tenant_id="tenant", document_id="doc", chunk_index=1,
            content="text", source="test", parent_id="parent", chunk_metadata='{"kind":"child"}',
        ))
        session.commit()
        rag = RAGService.__new__(RAGService)
        rag.session = session
        rag.tenant_id = "tenant"
        yield rag
    engine.dispose()


def _hit(key: str) -> ChunkResult:
    return ChunkResult(key, "text", "test", "doc", 0.0323, similarity=0.8)


async def test_child_expansion_reports_actual_relationship_and_origin(service):
    child, parent = await service._expand_parent_chunks([_hit("child")])
    assert child.parent_id == "parent"
    assert child.chunk_kind == "child"
    assert child.retrieval_origin == "hit"
    assert parent.chunk_kind == "parent"
    assert parent.retrieval_origin == "parent_expansion"
    assert parent.expanded_from_chunk_id == child.id
    assert parent.score == child.score
    assert parent.score_inherited_from_chunk_id == child.id


@pytest.mark.parametrize("keys", [("child", "parent"), ("parent", "child")])
async def test_direct_parent_hit_is_not_labelled_as_expansion(keys, service):
    hits = [_hit(key) for key in keys]
    for hit in hits:
        hit.score = 0.9 if hit.id == "parent" else 0.0323
    results = await service._expand_parent_chunks(hits)
    assert len(results) == 2
    parent = next(result for result in results if result.id == "parent")
    assert parent.retrieval_origin == "hit"
    assert parent.expanded_from_chunk_id is None
    assert parent.score_inherited_from_chunk_id is None
    assert parent.score == 0.9


async def test_missing_record_fails_closed(service):
    assert await service._expand_parent_chunks([_hit("missing")]) == []


async def test_search_api_preserves_explanation_fields(monkeypatch, service):
    from app.api.routes import rag as route

    results = await service._expand_parent_chunks([_hit("child")])

    async def fake_search(self, *args, **kwargs):
        return results

    monkeypatch.setattr(route.RAGService, "search", fake_search)
    output = await route.search(
        route.SearchRequest(query="test"),
        current_user=cast(Any, SimpleNamespace(tenant_id="tenant")),
        session=cast(Any, SimpleNamespace()),
    )
    assert output[0].chunk_id == "child"
    assert output[0].parent_id == "parent"
    assert output[1].retrieval_origin == "parent_expansion"
    assert output[1].expanded_from_chunk_id == "child"
    assert output[1].score_inherited_from_chunk_id == "child"
