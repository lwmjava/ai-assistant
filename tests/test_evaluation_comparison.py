from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.rag.vectorstore.base import ChunkResult
from app.schemas.evaluation import RetrievalVariant
from app.services.evaluation_comparison import (
    RetrievalComparisonConflictError,
    RetrievalComparisonService,
    RetrievalComparisonValidationError,
)


class FakeRag:
    def __init__(self) -> None:
        self.last_backend_name = ""
        self.calls: list[tuple[str, int, str]] = []
        self.identity_calls = 0
        self.drift = False
        self.fallback = False

    def supported_search_backends(self) -> list[str]:
        return ["native", "langchain"]

    def retrieval_identity(self) -> dict[str, str | int | None]:
        self.identity_calls += 1
        return {
            "identity_key": "local|mock|v1",
            "index_id": "index-b" if self.drift and self.identity_calls > 1 else "index-a",
        }

    async def search(self, query: str, top_k: int, *, backend: str) -> list[ChunkResult]:
        self.calls.append((query, top_k, backend))
        self.last_backend_name = "native" if self.fallback else backend
        return [
            ChunkResult(
                id=f"{backend}-chunk",
                content="authorized result",
                source="guide.md",
                document_id="doc-1",
                score=0.5,
                similarity=0.75,
            )
        ]


def service(fake: FakeRag) -> RetrievalComparisonService:
    return RetrievalComparisonService(fake)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_compare_reuses_identity_and_returns_ranked_results():
    fake = FakeRag()
    result = await service(fake).compare(
        " refund policy ",
        RetrievalVariant(backend="native", top_k=3),
        RetrievalVariant(backend="langchain", top_k=5),
    )
    assert fake.calls == [
        ("refund policy", 3, "native"),
        ("refund policy", 5, "langchain"),
    ]
    assert result.identity["index_id"] == "index-a"
    assert result.baseline.candidates[0].rank == 1
    assert result.candidate.effective_backend == "langchain"


@pytest.mark.asyncio
async def test_compare_rejects_identical_or_unavailable_variants():
    fake = FakeRag()
    same = RetrievalVariant(backend="native", top_k=3)
    with pytest.raises(RetrievalComparisonValidationError):
        await service(fake).compare("query", same, same)
    with pytest.raises(RetrievalComparisonValidationError):
        await service(fake).compare(
            "query", same, RetrievalVariant(backend="llamaindex", top_k=3)
        )
    assert fake.calls == []


@pytest.mark.asyncio
async def test_compare_rejects_fallback_and_identity_drift():
    fake = FakeRag()
    fake.fallback = True
    with pytest.raises(RetrievalComparisonConflictError):
        await service(fake).compare(
            "query",
            RetrievalVariant(backend="langchain", top_k=3),
            RetrievalVariant(backend="native", top_k=4),
        )

    drift = FakeRag()
    drift.drift = True
    with pytest.raises(RetrievalComparisonConflictError):
        await service(drift).compare(
            "query",
            RetrievalVariant(backend="native", top_k=3),
            RetrievalVariant(backend="langchain", top_k=4),
        )


def test_schema_rejects_invalid_top_k():
    with pytest.raises(ValidationError):
        RetrievalVariant(backend="native", top_k=0)

    with pytest.raises(ValidationError):
        RetrievalVariant.model_validate({"backend": "native", "top_k": 3, "rrf_k": 1})
