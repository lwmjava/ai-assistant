"""RAG 后端策略层：切分与检索可替换，嵌入与存储共用。"""

from app.rag.backend.base import BackendNotAvailableError, RagBackend
from app.rag.backend.factory import get_rag_backend
from app.rag.backend.native import NativeRagBackend

__all__ = [
    "BackendNotAvailableError",
    "RagBackend",
    "NativeRagBackend",
    "get_rag_backend",
]
