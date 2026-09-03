"""LangChain RAG 后端：中文 RecursiveCharacterTextSplitter + 项目 VectorStore 适配检索。

写入路径不交给 LangChain（``add_texts`` / ``from_texts`` 显式禁用），嵌入与向量库
仍共用项目 ``EmbeddingProvider`` + ``VectorStore``。
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from app.rag.backend.base import RagBackend
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.vectorstore.base import ChunkResult, VectorStore as ProjectVectorStore

logger = logging.getLogger(__name__)

# 关闭 LangSmith 默认遥测外发（见方案 §10）
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

# 中文友好切分符：默认 separators 无中文标点会导致整段不切分
_LC_SEPARATORS = [
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    "；",
    "，",
    "、",
    ".",
    "!",
    "?",
    ";",
    ",",
    " ",
    "",
]

Tokenizer = Callable[[str], list[str]]


class _ProjectEmbeddingAdapter(Embeddings):
    """项目 ``EmbeddingProvider`` → LangChain ``Embeddings``。"""

    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._provider.embed(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return (await self._provider.embed([text]))[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("同步嵌入会阻塞事件循环，请使用 aembed_documents")

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError("同步嵌入会阻塞事件循环，请使用 aembed_query")


class _ProjectVectorStoreAdapter(VectorStore):
    """项目 ``VectorStore`` → LangChain ``VectorStore``（只读检索）。"""

    def __init__(
        self,
        store: ProjectVectorStore,
        embedding: EmbeddingProvider,
        tokenizer: Tokenizer,
        tenant_id: str,
        rrf_k: int,
    ) -> None:
        self._store = store
        self._embedding = embedding
        self._tokenizer = tokenizer
        self._tenant_id = tenant_id
        self._rrf_k = rrf_k

    def add_texts(
        self,
        texts: list[str],
        metadatas: list[dict] | None = None,
        **kwargs: object,
    ) -> list[str]:
        raise NotImplementedError("写入统一走 RAGService，避免绕过主库与租户隔离")

    @classmethod
    def from_texts(
        cls,
        texts: list[str],
        embedding: Embeddings | None = None,
        metadatas: list[dict] | None = None,
        **kwargs: object,
    ) -> VectorStore:
        raise NotImplementedError("写入统一走 RAGService，避免绕过主库与租户隔离")

    def similarity_search(
        self,
        query: str,
        k: int = 4,
        **kwargs: object,
    ) -> list[Document]:
        raise NotImplementedError("请使用 asimilarity_search_with_score，避免同步阻塞事件循环")

    async def asimilarity_search_with_score(
        self,
        query: str,
        k: int = 4,
        **kwargs: object,
    ) -> list[tuple[Document, float]]:
        vec = (await self._embedding.embed([query]))[0]
        hits = await self._store.hybrid_search(
            query_embedding=vec,
            query_tokens=self._tokenizer(query),
            tenant_id=self._tenant_id,
            top_k=k,
            rrf_k=self._rrf_k,
        )
        return [
            (
                Document(
                    page_content=h.content,
                    metadata={
                        "chunk_id": h.id,
                        "document_id": h.document_id,
                        "source": h.source,
                    },
                ),
                h.score,
            )
            for h in hits
        ]


def _build_text_splitter(chunk_size: int, overlap: int, splitter: str):
    """按配置构造 LangChain TextSplitter（延迟导入）。"""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    name = (splitter or "recursive").strip().lower()
    if name != "recursive":
        logger.warning("未知 RAG_LANGCHAIN_SPLITTER=%s，使用 recursive", splitter)
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        length_function=len,
        separators=_LC_SEPARATORS,
    )


class LangChainRagBackend(RagBackend):
    """LangChain 后端：切分走 TextSplitter，检索走 VectorStore 适配器。"""

    name = "langchain"

    def __init__(
        self,
        embedding: EmbeddingProvider,
        vector_store: ProjectVectorStore,
        tokenizer: Tokenizer,
        *,
        rrf_k: int = 60,
        splitter: str = "recursive",
    ) -> None:
        self._embedding = embedding
        self._store = vector_store
        self._tokenizer = tokenizer
        self._rrf_k = rrf_k
        self._splitter_name = splitter

    async def split(self, text: str, *, chunk_size: int, overlap: int) -> list[str]:
        splitter = _build_text_splitter(chunk_size, overlap, self._splitter_name)

        def _run() -> list[str]:
            return splitter.split_text(text)

        return await asyncio.to_thread(_run)

    async def retrieve(
        self, query: str, *, tenant_id: str, top_k: int
    ) -> list[ChunkResult]:
        adapter = _ProjectVectorStoreAdapter(
            self._store,
            self._embedding,
            self._tokenizer,
            tenant_id,
            self._rrf_k,
        )
        pairs = await adapter.asimilarity_search_with_score(query, k=top_k)
        results: list[ChunkResult] = []
        for doc, score in pairs:
            meta = doc.metadata or {}
            results.append(
                ChunkResult(
                    id=str(meta.get("chunk_id") or ""),
                    content=doc.page_content,
                    source=meta.get("source"),
                    document_id=str(meta.get("document_id") or ""),
                    score=float(score),
                )
            )
        return results
