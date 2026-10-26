"""Authorized, identity-fenced side-by-side retrieval comparison."""

from __future__ import annotations

from time import perf_counter

from app.rag.service import RAGService
from app.rag.vectorstore.base import ChunkResult
from app.schemas.evaluation import (
    RetrievalComparisonCandidate,
    RetrievalComparisonOut,
    RetrievalComparisonRun,
    RetrievalVariant,
)


class RetrievalComparisonValidationError(ValueError):
    pass


class RetrievalComparisonConflictError(RuntimeError):
    pass


class RetrievalComparisonService:
    def __init__(self, rag: RAGService) -> None:
        self.rag = rag

    def strategies(self) -> list[str]:
        return self.rag.supported_search_backends()

    @staticmethod
    def _candidate(hit: ChunkResult, rank: int) -> RetrievalComparisonCandidate:
        return RetrievalComparisonCandidate(
            rank=rank,
            chunk_id=hit.id,
            document_id=hit.document_id,
            source=hit.source,
            score=hit.score,
            similarity=hit.similarity,
            version_status=hit.version_status,
            retrieval_origin=hit.retrieval_origin,
            parent_id=hit.parent_id,
            expanded_from_chunk_id=hit.expanded_from_chunk_id,
            score_inherited_from_chunk_id=hit.score_inherited_from_chunk_id,
            content_preview=hit.content[:500],
        )

    async def _run(self, query: str, variant: RetrievalVariant) -> RetrievalComparisonRun:
        started = perf_counter()
        hits = await self.rag.search(query, top_k=variant.top_k, backend=variant.backend)
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        if self.rag.last_backend_name != variant.backend:
            raise RetrievalComparisonConflictError("requested retrieval backend silently fell back")
        return RetrievalComparisonRun(
            variant=variant,
            effective_backend=self.rag.last_backend_name,
            elapsed_ms=elapsed_ms,
            candidates=[self._candidate(hit, rank) for rank, hit in enumerate(hits, 1)],
        )

    async def compare(
        self,
        query: str,
        baseline: RetrievalVariant,
        candidate: RetrievalVariant,
    ) -> RetrievalComparisonOut:
        query = query.strip()
        if not query:
            raise RetrievalComparisonValidationError("query must not be blank")
        if baseline == candidate:
            raise RetrievalComparisonValidationError("comparison variants must differ")
        supported = set(self.strategies())
        if baseline.backend not in supported or candidate.backend not in supported:
            raise RetrievalComparisonValidationError("retrieval backend is not available")

        before = self.rag.retrieval_identity()
        first = await self._run(query, baseline)
        middle = self.rag.retrieval_identity()
        second = await self._run(query, candidate)
        after = self.rag.retrieval_identity()
        if before != middle or before != after:
            raise RetrievalComparisonConflictError("retrieval identity changed during comparison")
        return RetrievalComparisonOut(identity=before, baseline=first, candidate=second)
