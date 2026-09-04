"""自研 RAG 后端：包装现有 ``split_text`` 与 ``hybrid_search``。"""

from collections.abc import Callable

from app.rag.backend.base import RagBackend
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.ingestion import split_text
from app.rag.vectorstore.base import ChunkResult, VectorStore

Tokenizer = Callable[[str], list[str]]


class NativeRagBackend(RagBackend):
    """默认后端：行为与接入策略层之前的自研管线一致。"""

    name = "native"

    def __init__(
        self,
        embedding: EmbeddingProvider,
        vector_store: VectorStore,
        tokenizer: Tokenizer,
        rrf_k: int = 60,
    ) -> None:
        self._embedding = embedding
        self._store = vector_store
        self._tokenizer = tokenizer
        self._rrf_k = rrf_k

    async def split(self, text: str, *, chunk_size: int, overlap: int) -> list[str]:
        return split_text(text, chunk_size, overlap)

    async def retrieve(
        self, query: str, *, tenant_id: str, top_k: int
    ) -> list[ChunkResult]:
        embedding = (await self._embedding.embed([query]))[0]
        tokens = self._tokenizer(query)
        return await self._store.hybrid_search(
            embedding, tokens, tenant_id, top_k, self._rrf_k
        )
