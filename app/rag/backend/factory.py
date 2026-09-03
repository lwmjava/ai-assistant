"""RAG 后端工厂。

提交 1 仅返回自研 ``native`` 后端，保证零依赖、行为不变。
后续提交按 ``RAG_BACKEND`` 路由 LangChain / LlamaIndex，缺依赖时降级 native。
"""

from collections.abc import Callable

from app.rag.backend.base import RagBackend
from app.rag.backend.native import NativeRagBackend
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.mock import tokenize
from app.rag.vectorstore.base import VectorStore

Tokenizer = Callable[[str], list[str]]


def get_rag_backend(
    embedding: EmbeddingProvider,
    vector_store: VectorStore,
    *,
    tokenizer: Tokenizer | None = None,
    rrf_k: int = 60,
) -> RagBackend:
    """构造当前生效的 RAG 后端（本阶段固定为 native）。"""
    return NativeRagBackend(
        embedding,
        vector_store,
        tokenizer=tokenizer or tokenize,
        rrf_k=rrf_k,
    )
