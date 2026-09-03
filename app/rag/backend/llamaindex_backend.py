"""LlamaIndex RAG 后端占位（提交 3 填实现）。

工厂在 ``RAG_BACKEND=llamaindex`` 时会尝试加载本模块；当前仅抛出
``BackendNotAvailableError``，由工厂降级为 native。
"""

from __future__ import annotations

from collections.abc import Callable

from app.rag.backend.base import BackendNotAvailableError, RagBackend
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.vectorstore.base import ChunkResult, VectorStore

Tokenizer = Callable[[str], list[str]]


class LlamaIndexRagBackend(RagBackend):
    """LlamaIndex 后端骨架（尚未实现）。"""

    name = "llamaindex"

    def __init__(
        self,
        embedding: EmbeddingProvider,
        vector_store: VectorStore,
        tokenizer: Tokenizer,
        *,
        rrf_k: int = 60,
    ) -> None:
        raise BackendNotAvailableError(
            "LlamaIndex 后端尚未实现。请安装 ai-assistant[llamaindex] 并等待提交 3，"
            "或设置 RAG_BACKEND=native|langchain。"
        )

    async def split(self, text: str, *, chunk_size: int, overlap: int) -> list[str]:
        raise BackendNotAvailableError("LlamaIndex 后端尚未实现")

    async def retrieve(
        self, query: str, *, tenant_id: str, top_k: int
    ) -> list[ChunkResult]:
        raise BackendNotAvailableError("LlamaIndex 后端尚未实现")
