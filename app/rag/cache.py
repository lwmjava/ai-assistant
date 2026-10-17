"""Optional retrieval cache; authorization and identity checks precede every hit."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from app.core.config import settings
from app.core.response_cache import BoundedCache, digest, object_namespace
from app.models.user import User
from app.rag.access import ReadScope
from app.rag.backend.base import RagBackend
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.index_identity import identity_from_provider
from app.rag.index_registry import resolve_read_index_for
from app.rag.resilience import current_deadline, ensure_budget
from app.rag.vectorstore.base import ChunkResult, VectorStore
from app.services.cache_scope import AuthorizedCacheScope

retrieval_cache = BoundedCache()


def _implementation(value: object) -> str:
    """Standard implementations share across requests; custom instances stay isolated."""
    cls = type(value)
    standard = {
        ("app.rag.backend.native", "NativeRagBackend"),
        ("app.rag.backend.langchain_backend", "LangChainRagBackend"),
        ("app.rag.backend.llamaindex_backend", "LlamaIndexRagBackend"),
        ("app.rag.vectorstore.local", "LocalVectorStore"),
        ("app.rag.vectorstore.milvus", "MilvusVectorStore"),
    }
    name = f"{cls.__module__}.{cls.__qualname__}"
    return name if (cls.__module__, cls.__name__) in standard else name + object_namespace(value)


def _embedding_options(provider: EmbeddingProvider) -> object:
    cls = type(provider)
    standard = cls.__module__ in {"app.rag.embeddings.mock", "app.rag.embeddings.openai_compatible"}
    return (
        f"{cls.__module__}.{cls.__qualname__}",
        None if standard else object_namespace(provider),
        None if standard else vars(provider),
        {name: getattr(provider, name, None) for name in (
            "model", "dim", "base_url", "api_key", "timeout", "batch_size", "input_policy", "_seed",
        )},
    )


class CachedRagBackend(RagBackend):
    """Service-installed decorator, covering both API search and Agent retrievers."""

    def __init__(
        self,
        backend: RagBackend,
        session: Session,
        tenant_id: str,
        reader: User | None,
        embedding: EmbeddingProvider,
        store: VectorStore,
        tokenizer: Callable[[str], list[str]],
    ) -> None:
        self.backend = backend
        self.name = backend.name
        self.session = session
        self.tenant_id = tenant_id
        self.reader = reader
        self.embedding = embedding
        self.store = store
        self.tokenizer = tokenizer
        self._scope: AuthorizedCacheScope | None = None

    def __getattr__(self, name: str) -> Any:
        # Preserve existing backend introspection/instrumentation interfaces.
        return getattr(self.backend, name)

    async def split(self, text: str, *, chunk_size: int, overlap: int) -> list[str]:
        return await self.backend.split(text, chunk_size=chunk_size, overlap=overlap)

    def _identity_key(self, scope: AuthorizedCacheScope) -> tuple[str, str | None]:
        identity = identity_from_provider(self.embedding)
        with Session(scope.bind) as fresh:
            index = resolve_read_index_for(fresh, identity, tenant_id=self.tenant_id, backend=identity.backend)
            return identity.key(), index.id if index is not None else None

    async def retrieve(
        self,
        query: str,
        *,
        tenant_id: str,
        top_k: int,
        read_scope: ReadScope | None = None,
    ) -> list[ChunkResult]:
        async def original() -> list[ChunkResult]:
            return await self.backend.retrieve(query, tenant_id=tenant_id, top_k=top_k, read_scope=read_scope)

        registered = (type(self.backend).__module__, type(self.backend).__name__) in {
            ("app.rag.backend.native", "NativeRagBackend"),
            ("app.rag.backend.langchain_backend", "LangChainRagBackend"),
            ("app.rag.backend.llamaindex_backend", "LlamaIndexRagBackend"),
        }
        actual_dependencies_match = (
            getattr(self.backend, "_embedding", None) is self.embedding
            and getattr(self.backend, "_store", None) is self.store
            and getattr(self.backend, "_tokenizer", None) is self.tokenizer
        )
        if not settings.RETRIEVAL_CACHE_ENABLED or not registered or not actual_dependencies_match:
            return await original()
        if self._scope is None:
            self._scope = AuthorizedCacheScope(self.session, self.reader, self.tenant_id)
        scope = self._scope
        if tenant_id != self.tenant_id or read_scope != scope.read_scope:
            raise PermissionError("检索缓存请求的主体范围不一致")
        before = scope.snapshot(query)
        if before is None or isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            return await original()
        deadline = current_deadline()
        if deadline is not None:
            ensure_budget(deadline, backend=self.name, tenant=tenant_id)
        identity, index_id = self._identity_key(scope)
        key = digest(
            (
                before,
                identity,
                index_id,
                query,
                top_k,
                _implementation(self.backend),
                _implementation(self.store),
                self.tokenizer(query),
                getattr(self.backend, "_rrf_k", None),
                _embedding_options(self.embedding),
            )
        )
        cached = retrieval_cache.get(key)
        if cached is not None and scope.validate_hits(cached, query, index_id):
            # SQL authorization/visibility may change during either lookup.
            if scope.snapshot(query) == before and self._identity_key(scope) == (identity, index_id):
                if deadline is not None:
                    ensure_budget(deadline, backend=self.name, tenant=tenant_id)
                return cached
        hits = await original()
        after = scope.snapshot(query)  # Raises on revocation even after a backend await.
        if not scope.validate_hits(hits, query, self._identity_key(scope)[1]):
            raise PermissionError("检索结果已失效，请重试检索")
        if after == before and all(math.isfinite(h.score) and math.isfinite(h.similarity) for h in hits):
            retrieval_cache.put(key, hits)
        return hits
