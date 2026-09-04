"""RAG 多后端策略层测试。"""

from __future__ import annotations

import builtins

import pytest
from sqlmodel import Session

from app.core.config import settings
from app.rag.backend.factory import get_rag_backend, normalize_rag_backend
from app.rag.backend.native import NativeRagBackend
from app.rag.embeddings.mock import MockEmbeddingProvider, tokenize
from app.rag.service import RAGService


@pytest.fixture()
def session():
    from app.core.database import engine

    with Session(engine) as s:
        yield s


def test_normalize_unknown_backend_falls_back_to_native() -> None:
    assert normalize_rag_backend("unknown-backend") == "native"


def test_factory_defaults_native() -> None:
    emb = MockEmbeddingProvider(dim=8)
    backend = get_rag_backend(emb, vector_store=None)  # type: ignore[arg-type]
    assert isinstance(backend, NativeRagBackend)
    assert backend.name == "native"


def test_factory_langchain_without_deps_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = MockEmbeddingProvider(dim=8)
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.rag.backend.langchain_backend":
            raise ImportError("simulated missing langchain extras")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    backend = get_rag_backend(emb, vector_store=None, backend="langchain")  # type: ignore[arg-type]
    assert isinstance(backend, NativeRagBackend)


langchain_core = pytest.importorskip("langchain_core")


async def test_langchain_split_produces_chunks() -> None:
    from app.rag.backend.langchain_backend import LangChainRagBackend

    backend = LangChainRagBackend(
        MockEmbeddingProvider(dim=8),
        vector_store=None,  # type: ignore[arg-type]
        tokenizer=tokenize,
        splitter="recursive",
    )
    text = "第一句。第二句。第三句很长需要被保留。" * 10
    chunks = await backend.split(text, chunk_size=50, overlap=10)
    assert chunks
    assert all(isinstance(c, str) and c.strip() for c in chunks)


async def test_langchain_split_can_differ_from_native() -> None:
    """同一文本两种切分策略块数/边界可能不同，用于横向对比。"""
    from app.rag.backend.langchain_backend import LangChainRagBackend

    text = "第一句。第二句。第三句很长需要被保留。" * 10
    native = NativeRagBackend(
        MockEmbeddingProvider(dim=8),
        vector_store=None,  # type: ignore[arg-type]
        tokenizer=tokenize,
    )
    lc = LangChainRagBackend(
        MockEmbeddingProvider(dim=8),
        vector_store=None,  # type: ignore[arg-type]
        tokenizer=tokenize,
    )
    native_chunks = await native.split(text, chunk_size=50, overlap=10)
    lc_chunks = await lc.split(text, chunk_size=50, overlap=10)
    assert native_chunks
    assert lc_chunks
    # 不强制不同，但两者都应产出有效块
    assert sum(len(c) for c in native_chunks) > 0
    assert sum(len(c) for c in lc_chunks) > 0


async def test_langchain_retrieve_returns_chunk_result(session: Session) -> None:
    rag = RAGService(session, "lc-tenant", embedding_provider=MockEmbeddingProvider(dim=64))
    lc = get_rag_backend(
        rag._embedding,
        rag._vector_store,
        backend="langchain",
        tokenizer=tokenize,
    )
    await rag.ingest_text(
        "LangChain 后端检索测试：混合检索结合稠密向量与 BM25。",
        title="LC 测试",
        source="lc",
        user_id="lc-user",
        backend="langchain",
    )
    hits = await lc.retrieve("混合检索", tenant_id="lc-tenant", top_k=3)
    assert hits
    assert all(hasattr(h, "document_id") and hasattr(h, "score") for h in hits)


def test_vector_store_adapter_blocks_writes() -> None:
    pytest.importorskip("langchain_core")
    from app.rag.backend.langchain_backend import _ProjectVectorStoreAdapter

    adapter = _ProjectVectorStoreAdapter(
        store=None,  # type: ignore[arg-type]
        embedding=MockEmbeddingProvider(dim=8),
        tokenizer=tokenize,
        tenant_id="t1",
        rrf_k=60,
    )
    with pytest.raises(NotImplementedError, match="RAGService"):
        adapter.add_texts(["hello"])
    with pytest.raises(NotImplementedError, match="RAGService"):
        adapter.from_texts(["hello"])


async def test_ingest_request_backend_override(session: Session) -> None:
    original = settings.RAG_BACKEND
    try:
        settings.RAG_BACKEND = "native"
        # 显式注入 Mock 嵌入，避免测试用例依赖真实嵌入服务与网络
        rag = RAGService(
            session, "override-tenant", embedding_provider=MockEmbeddingProvider(dim=64)
        )
        doc = await rag.ingest_text(
            "请求级 backend 覆盖测试。",
            title="override",
            source="test",
            user_id="u1",
            backend="langchain",
        )
        assert doc.chunk_count > 0
    finally:
        settings.RAG_BACKEND = original


def test_factory_llamaindex_without_deps_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    emb = MockEmbeddingProvider(dim=8)
    real_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.rag.backend.llamaindex_backend":
            raise ImportError("simulated missing llamaindex extras")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    backend = get_rag_backend(emb, vector_store=None, backend="llamaindex")  # type: ignore[arg-type]
    assert isinstance(backend, NativeRagBackend)


llama_index_core = pytest.importorskip("llama_index.core", reason="需要 pip install ai-assistant[llamaindex]")


async def test_llamaindex_split_produces_chunks() -> None:
    from app.rag.backend.llamaindex_backend import LlamaIndexRagBackend

    backend = LlamaIndexRagBackend(
        MockEmbeddingProvider(dim=8),
        vector_store=None,  # type: ignore[arg-type]
        tokenizer=tokenize,
        splitter="sentence",
    )
    text = "第一句。第二句。第三句很长需要被保留。" * 10
    chunks = await backend.split(text, chunk_size=50, overlap=10)
    assert chunks
    assert all(isinstance(c, str) and c.strip() for c in chunks)


async def test_llamaindex_retrieve_returns_chunk_result(session: Session) -> None:
    rag = RAGService(session, "li-tenant", embedding_provider=MockEmbeddingProvider(dim=64))
    li = get_rag_backend(
        rag._embedding,
        rag._vector_store,
        backend="llamaindex",
        tokenizer=tokenize,
    )
    await rag.ingest_text(
        "LlamaIndex 后端检索测试：混合检索结合稠密向量与 BM25。",
        title="LI 测试",
        source="li",
        user_id="li-user",
        backend="llamaindex",
    )
    hits = await li.retrieve("混合检索", tenant_id="li-tenant", top_k=3)
    assert hits
    assert all(hasattr(h, "document_id") and hasattr(h, "score") for h in hits)


def test_llamaindex_vector_store_blocks_writes() -> None:
    from app.rag.backend.llamaindex_backend import _ProjectPydanticVectorStore

    adapter = _ProjectPydanticVectorStore(
        store=None,  # type: ignore[arg-type]
        embedding=MockEmbeddingProvider(dim=8),
        tokenizer=tokenize,
        tenant_id="t1",
        rrf_k=60,
    )
    with pytest.raises(NotImplementedError, match="RAGService"):
        adapter.vector_store.add([])
    with pytest.raises(NotImplementedError, match="RAGService"):
        adapter.vector_store.delete("doc-1")
