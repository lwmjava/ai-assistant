"""RAG 后端策略层：切分与检索可替换，嵌入与存储共用。"""

from app.rag.backend.base import BackendNotAvailableError, RagBackend
from app.rag.backend.factory import VALID_RAG_BACKENDS, get_rag_backend, normalize_rag_backend
from app.rag.backend.native import NativeRagBackend

__all__ = [
    "BackendNotAvailableError",
    "RagBackend",
    "NativeRagBackend",
    "VALID_RAG_BACKENDS",
    "normalize_rag_backend",
    "get_rag_backend",
]


def __getattr__(name: str):
    if name == "LangChainRagBackend":
        from app.rag.backend.langchain_backend import LangChainRagBackend

        return LangChainRagBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
