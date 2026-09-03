"""LlamaIndex RAG 后端骨架：SentenceSplitter + 项目 VectorStore 混合检索。

写入路径不交给 LlamaIndex（``add`` / ``delete`` 显式禁用），嵌入与向量库仍共用
项目 ``EmbeddingProvider`` + ``VectorStore``。检索骨架阶段直接委托 ``hybrid_search``，
与 LangChain 适配器一致；后续可在此接入 ``VectorIndexRetriever``。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from app.rag.backend.base import RagBackend
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.vectorstore.base import ChunkResult, VectorStore as ProjectVectorStore

logger = logging.getLogger(__name__)

Tokenizer = Callable[[str], list[str]]


class _ProjectLlamaEmbedding:
    """项目 ``EmbeddingProvider`` → LlamaIndex ``BaseEmbedding``（延迟继承）。"""

    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider
        self._cls = self._load_base_class()
        self._impl = self._build_impl(provider)

    @staticmethod
    def _load_base_class():
        from llama_index.core.embeddings import BaseEmbedding

        return BaseEmbedding

    @classmethod
    def _build_impl(cls, provider: EmbeddingProvider):
        base = cls._load_base_class()

        class _Impl(base):  # type: ignore[misc, valid-type]
            def __init__(self, inner: EmbeddingProvider) -> None:
                super().__init__()
                self._inner = inner

            async def _aget_query_embedding(self, query: str) -> list[float]:
                return (await self._inner.embed([query]))[0]

            async def _aget_text_embedding(self, text: str) -> list[float]:
                return (await self._inner.embed([text]))[0]

            async def _aget_text_embeddings(self, texts: list[str]) -> list[list[float]]:
                return await self._inner.embed(texts)

            def _get_query_embedding(self, query: str) -> list[float]:
                raise NotImplementedError("同步嵌入会阻塞事件循环，请使用 async 接口")

            def _get_text_embedding(self, text: str) -> list[float]:
                raise NotImplementedError("同步嵌入会阻塞事件循环，请使用 async 接口")

            def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
                raise NotImplementedError("同步嵌入会阻塞事件循环，请使用 async 接口")

        return _Impl(provider)

    @property
    def embedding(self):
        return self._impl


class _ProjectPydanticVectorStore:
    """项目 ``VectorStore`` → LlamaIndex ``BasePydanticVectorStore``（只读，延迟继承）。"""

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
        self._impl = self._build_impl(store, embedding, tokenizer, tenant_id, rrf_k)

    @staticmethod
    def _build_impl(
        store: ProjectVectorStore,
        embedding: EmbeddingProvider,
        tokenizer: Tokenizer,
        tenant_id: str,
        rrf_k: int,
    ):
        from llama_index.core.schema import BaseNode
        from llama_index.core.vector_stores.types import (
            BasePydanticVectorStore,
            VectorStoreQuery,
            VectorStoreQueryResult,
        )

        class _Impl(BasePydanticVectorStore):  # type: ignore[misc]
            stores_text: bool = True

            @property
            def client(self) -> Any:
                """骨架适配器无独立客户端，返回底层项目 VectorStore。"""
                return store

            def add(self, nodes: list[BaseNode], **add_kwargs: Any) -> list[str]:
                raise NotImplementedError("写入统一走 RAGService，避免绕过主库与租户隔离")

            def delete(self, ref_doc_id: str, **delete_kwargs: Any) -> None:
                raise NotImplementedError("写入统一走 RAGService，避免绕过主库与租户隔离")

            def query(self, query: VectorStoreQuery, **kwargs: Any) -> VectorStoreQueryResult:
                raise NotImplementedError(
                    "骨架阶段检索走 hybrid_search 适配器，请使用 LlamaIndexRagBackend.retrieve"
                )

        return _Impl()

    @property
    def vector_store(self):
        return self._impl

    async def hybrid_search(self, query: str, top_k: int) -> list[ChunkResult]:
        """骨架检索：复用项目混合检索（与 native / LangChain 共用向量库）。"""
        vec = (await self._embedding.embed([query]))[0]
        return await self._store.hybrid_search(
            query_embedding=vec,
            query_tokens=self._tokenizer(query),
            tenant_id=self._tenant_id,
            top_k=top_k,
            rrf_k=self._rrf_k,
        )


def _build_node_parser(chunk_size: int, overlap: int, splitter: str):
    """按配置构造 LlamaIndex NodeParser（延迟导入）。"""
    name = (splitter or "sentence").strip().lower()
    if name == "markdown":
        from llama_index.core.node_parser import MarkdownNodeParser

        return MarkdownNodeParser()
    if name != "sentence":
        logger.warning("未知 RAG_LLAMAINDEX_SPLITTER=%s，使用 sentence", splitter)
    from llama_index.core.node_parser import SentenceSplitter

    return SentenceSplitter(chunk_size=chunk_size, chunk_overlap=overlap)


class LlamaIndexRagBackend(RagBackend):
    """LlamaIndex 后端：切分走 NodeParser，检索走 hybrid_search 适配。"""

    name = "llamaindex"

    def __init__(
        self,
        embedding: EmbeddingProvider,
        vector_store: ProjectVectorStore,
        tokenizer: Tokenizer,
        *,
        rrf_k: int = 60,
        splitter: str = "sentence",
    ) -> None:
        self._embedding = embedding
        self._store = vector_store
        self._tokenizer = tokenizer
        self._rrf_k = rrf_k
        self._splitter_name = splitter
        # 预构造适配器，验证 LlamaIndex 依赖可用且接口对称
        _ProjectLlamaEmbedding(embedding)
        self._vector_adapter = _ProjectPydanticVectorStore(
            vector_store, embedding, tokenizer, tenant_id="", rrf_k=rrf_k
        )

    async def split(self, text: str, *, chunk_size: int, overlap: int) -> list[str]:
        from llama_index.core.schema import Document as LIDocument

        parser = _build_node_parser(chunk_size, overlap, self._splitter_name)

        def _run() -> list[str]:
            nodes = parser.get_nodes_from_documents([LIDocument(text=text)])
            return [node.get_content() for node in nodes if node.get_content().strip()]

        return await asyncio.to_thread(_run)

    async def retrieve(
        self, query: str, *, tenant_id: str, top_k: int
    ) -> list[ChunkResult]:
        adapter = _ProjectPydanticVectorStore(
            self._store,
            self._embedding,
            self._tokenizer,
            tenant_id,
            self._rrf_k,
        )
        return await adapter.hybrid_search(query, top_k)
