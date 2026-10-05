"""检索适配保留融合分与稠密相似度的独立语义。"""
import pytest

pytest.importorskip("langchain_core")

from app.core.config import settings  # noqa: E402
from app.rag.backend.langchain_backend import LangChainRagBackend  # noqa: E402
from app.rag.embeddings.mock import MockEmbeddingProvider, tokenize  # noqa: E402
from app.rag.retriever import HybridRetriever  # noqa: E402
from app.rag.vectorstore.base import ChunkResult  # noqa: E402


class FakeStore:
    async def hybrid_search(self, **kwargs):
        return [
            ChunkResult("high", "有效证据", "source", "doc", 0.03, similarity=0.9),
            ChunkResult("low", "不相关内容", "source", "doc", 0.02, similarity=0.1),
        ]


def test_adapter_rejects_generator_and_classmethod_writes():
    from app.rag.backend.langchain_backend import _ProjectVectorStoreAdapter

    embedding = MockEmbeddingProvider(dim=8)
    adapter = _ProjectVectorStoreAdapter(FakeStore(), embedding, tokenize, "tenant", 60)
    with pytest.raises(NotImplementedError, match="RAGService"):
        adapter.add_texts(iter(["text"]), ids=["id"])
    with pytest.raises(NotImplementedError, match="RAGService"):
        _ProjectVectorStoreAdapter.from_texts(["text"], embedding, ids=["id"])
    with pytest.raises(NotImplementedError, match="RAGService"):
        _ProjectVectorStoreAdapter.from_texts(["text"])


async def test_retrieval_preserves_scores_and_threshold(monkeypatch):
    monkeypatch.setattr(settings, "RAG_MIN_SIMILARITY", 0.4)
    backend = LangChainRagBackend(MockEmbeddingProvider(dim=8), FakeStore(), tokenize)
    hits = await backend.retrieve("查询", tenant_id="tenant", top_k=2)
    assert [(h.score, h.similarity) for h in hits] == [(0.03, 0.9), (0.02, 0.1)]
    assert [(h.id, h.document_id, h.source, h.version_status) for h in hits] == [
        ("high", "doc", "source", "current"),
        ("low", "doc", "source", "current"),
    ]
    retriever = HybridRetriever(backend, "tenant", top_k=2)
    context = await retriever.retrieve("查询", "")
    assert "有效证据" in context
    assert "不相关内容" not in context
    assert [h.id for h in retriever.last_hits] == ["high"]


@pytest.mark.parametrize("value", [None, "invalid", float("nan"), float("inf"), 1.1, -1.1, True])
async def test_invalid_similarity_rejected(monkeypatch, value):
    from langchain_core.documents import Document

    from app.rag.backend.langchain_backend import _ProjectVectorStoreAdapter

    async def fake_search(self, query, k=4, **kwargs):
        return [(Document(page_content="证据", metadata={"similarity": value}), 0.03)]

    monkeypatch.setattr(_ProjectVectorStoreAdapter, "asimilarity_search_with_score", fake_search)
    backend = LangChainRagBackend(MockEmbeddingProvider(dim=8), FakeStore(), tokenize)
    with pytest.raises(ValueError, match="similarity"):
        await backend.retrieve("查询", tenant_id="tenant", top_k=1)


async def test_missing_similarity_rejected(monkeypatch):
    from langchain_core.documents import Document

    from app.rag.backend.langchain_backend import _ProjectVectorStoreAdapter

    async def fake_search(self, query, k=4, **kwargs):
        return [(Document(page_content="证据", metadata={}), 0.03)]

    monkeypatch.setattr(_ProjectVectorStoreAdapter, "asimilarity_search_with_score", fake_search)
    backend = LangChainRagBackend(MockEmbeddingProvider(dim=8), FakeStore(), tokenize)
    with pytest.raises(ValueError, match="similarity"):
        await backend.retrieve("查询", tenant_id="tenant", top_k=1)


@pytest.mark.parametrize("value", [-1, 0, 1])
async def test_similarity_boundary_preserved(monkeypatch, value):
    from langchain_core.documents import Document

    from app.rag.backend.langchain_backend import _ProjectVectorStoreAdapter

    async def fake_search(self, query, k=4, **kwargs):
        return [(Document(page_content="证据", metadata={"similarity": value}), 0.03)]

    monkeypatch.setattr(_ProjectVectorStoreAdapter, "asimilarity_search_with_score", fake_search)
    backend = LangChainRagBackend(MockEmbeddingProvider(dim=8), FakeStore(), tokenize)
    hits = await backend.retrieve("查询", tenant_id="tenant", top_k=1)
    assert hits[0].similarity == value
    assert hits[0].score == 0.03
