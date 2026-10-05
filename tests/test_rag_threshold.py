"""PRAG-004 检索低分阈值与拒答：HybridRetriever 过滤逻辑与拒答提示。"""

from app.core.config import settings
from app.rag.retriever import HybridRetriever
from app.rag.vectorstore.base import ChunkResult


class _FakeBackend:
    def __init__(self, hits: list[ChunkResult]) -> None:
        self._hits = hits

    async def retrieve(self, query: str, *, tenant_id: str, top_k: int) -> list[ChunkResult]:
        return self._hits


def _hit(similarity: float, content: str = "x") -> ChunkResult:
    return ChunkResult(
        id=content,
        content=content,
        source="doc-1",
        document_id="doc-1",
        score=0.01,
        similarity=similarity,
    )


def _retriever(hits: list[ChunkResult]) -> HybridRetriever:
    return HybridRetriever(_FakeBackend(hits), tenant_id="t", top_k=5)


async def test_retriev_keeps_all_when_above_threshold() -> None:
    hits = [_hit(0.9, "a"), _hit(0.8, "b")]
    r = _retriever(hits)
    out = await r.retrieve("q", "")
    assert "a" in out and "b" in out
    assert [h.id for h in r.last_hits] == ["a", "b"]


async def test_retriev_filters_below_threshold() -> None:
    hits = [_hit(0.9, "a"), _hit(0.2, "b")]
    r = _retriever(hits)
    out = await r.retrieve("q", "")
    assert "a" in out and "b" not in out
    assert [h.id for h in r.last_hits] == ["a"]


async def test_retriev_refuses_when_all_below_threshold() -> None:
    hits = [_hit(0.2, "a"), _hit(0.1, "b")]
    r = _retriever(hits)
    out = await r.retrieve("q", "")
    assert out == settings.RAG_REFUSE_MESSAGE
    assert r.last_hits == []


async def test_retriev_empty_when_no_results() -> None:
    r = _retriever([])
    assert await r.retrieve("q", "") == ""
    assert r.last_hits == []
