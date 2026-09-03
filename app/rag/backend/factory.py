"""RAG 后端工厂。

按 ``RAG_BACKEND`` 或请求级覆盖路由 native / langchain / llamaindex。
缺依赖或未知值时降级 native 并记录 warning，不阻断主路径。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.core.config import settings
from app.rag.backend.base import RagBackend
from app.rag.backend.native import NativeRagBackend
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.mock import tokenize
from app.rag.vectorstore.base import VectorStore

logger = logging.getLogger(__name__)

Tokenizer = Callable[[str], list[str]]

VALID_RAG_BACKENDS = frozenset({"native", "langchain", "llamaindex"})


def normalize_rag_backend(name: str | None) -> str:
    """规范化后端名；未知值返回 ``native``。"""
    backend = (name or settings.RAG_BACKEND or "native").strip().lower()
    if backend not in VALID_RAG_BACKENDS:
        logger.warning("未知 RAG 后端 %r，降级为 native", backend)
        return "native"
    return backend


def get_rag_backend(
    embedding: EmbeddingProvider,
    vector_store: VectorStore,
    *,
    backend: str | None = None,
    tokenizer: Tokenizer | None = None,
    rrf_k: int | None = None,
) -> RagBackend:
    """构造当前生效的 RAG 后端。"""
    name = normalize_rag_backend(backend)
    tok = tokenizer or tokenize
    rrf = rrf_k if rrf_k is not None else settings.RAG_HYBRID_RRF_K

    if name == "langchain":
        try:
            from app.rag.backend.langchain_backend import LangChainRagBackend

            return LangChainRagBackend(
                embedding,
                vector_store,
                tok,
                rrf_k=rrf,
                splitter=settings.RAG_LANGCHAIN_SPLITTER,
            )
        except ImportError as exc:
            logger.warning(
                "RAG_BACKEND=langchain 但未安装 langchain extras（%s），降级 native。"
                "请执行 pip install \"ai-assistant[langchain]\"",
                exc,
            )
            name = "native"

    if name == "llamaindex":
        try:
            from app.rag.backend.llamaindex_backend import LlamaIndexRagBackend

            return LlamaIndexRagBackend(
                embedding,
                vector_store,
                tok,
                rrf_k=rrf,
                splitter=settings.RAG_LLAMAINDEX_SPLITTER,
            )
        except ImportError as exc:
            logger.warning(
                "RAG_BACKEND=llamaindex 但未安装 llamaindex extras（%s），降级 native。"
                "请执行 pip install \"ai-assistant[llamaindex]\"",
                exc,
            )
            name = "native"
        except Exception as exc:  # noqa: BLE001 — 适配器构造失败不应阻断主路径
            logger.warning("RAG_BACKEND=llamaindex 初始化失败（%s），降级 native", exc)
            name = "native"

    return NativeRagBackend(embedding, vector_store, tokenizer=tok, rrf_k=rrf)
