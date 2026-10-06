"""Input policy and ingestion must preserve excluded content without sending it."""

import json

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.rag import DocumentChunk
from app.rag.chunking.base import Chunk
from app.rag.document_parsers.base import ParsedDocument
from app.rag.embeddings.base import EmbeddingInputPolicy
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.service import RAGService


class RecordingEmbedding(MockEmbeddingProvider):
    def __init__(self, limit=40):
        super().__init__(dim=4)
        self.input_policy = EmbeddingInputPolicy(
            max_input_tokens=limit, counter=len, counting_method="test-characters",
            source="synthetic-test", version="test-v1",
        )
        self.calls = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        assert all(self.input_policy.check(text) is None for text in texts)
        return await super().embed(texts)


def test_policy_does_not_guess_unknown_counter():
    policy = EmbeddingInputPolicy(max_input_tokens=8192, counter=None)
    assert policy.check("hello") == "input_count_unverified"


def test_policy_checks_margin_and_exact_boundary():
    policy = EmbeddingInputPolicy(max_input_tokens=10, counter=len, safety_margin=2)
    assert policy.check("x" * 8) is None
    assert policy.check("x" * 9) == "input_limit_exceeded"


def test_dashscope_policy_is_scoped_to_verified_endpoint_and_model():
    from app.rag.embeddings.input_limits import resolve_input_policy

    policy = resolve_input_policy("https://dashscope.aliyuncs.com/compatible-mode/v1", "text-embedding-v3")
    assert policy and policy.max_input_tokens == 8192
    assert policy.check("x" * 8160) is None
    assert policy.check("x" * 8161) == "input_limit_exceeded"
    assert policy.check("中" * 2721) == "input_limit_exceeded"
    assert resolve_input_policy("https://custom.example/v1", "text-embedding-v3") is None
    assert resolve_input_policy("https://dashscope.aliyuncs.com/compatible-mode/v1", "unknown") is None


async def test_real_provider_rejects_unknown_and_exceeded_inputs_before_http(monkeypatch):
    import httpx

    from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider

    def no_http(**kwargs):
        raise AssertionError("HTTP must not be created for excluded inputs")

    monkeypatch.setattr(httpx, "AsyncClient", no_http)
    unknown = OpenAICompatibleEmbeddingProvider("https://custom.example/v1", "fixture", "unknown")
    with pytest.raises(ValueError, match="embedding_input_limit_unverified"):
        await unknown.embed(["synthetic"])
    known = OpenAICompatibleEmbeddingProvider(
        "https://dashscope.aliyuncs.com/compatible-mode/v1", "fixture", "text-embedding-v3", batch_size=100,
    )
    assert known.batch_size == 10
    with pytest.raises(ValueError, match="embedding_input_limit_exceeded"):
        await known.embed(["x" * 8161])


@pytest.fixture()
def isolated_session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


async def test_persist_skips_oversized_but_saves_original_and_alignment(isolated_session):
    provider = RecordingEmbedding()
    service = RAGService(isolated_session, tenant_id="limit-test", embedding_provider=provider)
    text = "```\n" + "x" * 80 + "\n```"
    doc = await service._persist_document(
        title="synthetic", source="fixture", user_id="test-user",
        chunk_objs=[Chunk(text="before", index=0), Chunk(text=text, index=1),
                    Chunk(text="after", index=2)], strategy_name="structured",
    )
    rows = isolated_session.exec(select(DocumentChunk).where(
        DocumentChunk.document_id == doc.id
    ).order_by(DocumentChunk.chunk_index)).all()
    assert provider.calls == [["before", "after"]]
    assert [row.content for row in rows] == ["before", text, "after"]
    assert rows[0].embedding and rows[2].embedding
    assert rows[1].embedding is None
    metadata = json.loads(rows[1].chunk_metadata)
    assert metadata["embedding_status"] == "not_vectorized"
    assert metadata["embedding_skip_reason"] == "input_limit_exceeded"
    assert metadata["oversized"] is True


async def test_all_excluded_chunks_do_not_make_empty_provider_call(isolated_session):
    provider = RecordingEmbedding(limit=1)
    service = RAGService(isolated_session, tenant_id="limit-test", embedding_provider=provider)
    doc = await service._persist_document(
        title="synthetic", source="fixture", user_id="test-user",
        chunk_objs=[Chunk(text="unbroken-original", index=0)], strategy_name="structured",
    )
    assert doc.chunk_count == 1
    assert provider.calls == []


async def test_reparse_preserves_excluded_structure(isolated_session):
    provider = RecordingEmbedding()
    service = RAGService(isolated_session, tenant_id="limit-test", embedding_provider=provider)
    doc = await service.ingest_text("old", "synthetic", "fixture", "test-user")
    provider.calls.clear()
    original = "```\n" + "x" * 80 + "\n```\n"
    parsed = ParsedDocument(text=original, title="synthetic", source="fixture",
                            extension="md", content_type="text/markdown")
    await service.reindex_document_in_place(doc, parsed, content_hash="synthetic-hash")
    rows = isolated_session.exec(select(DocumentChunk).where(
        DocumentChunk.document_id == doc.id
    )).all()
    assert provider.calls == []
    assert "".join(row.content for row in rows) == original
    assert all(row.embedding is None for row in rows)
