"""嵌入模型层（OpenAI/Ollama 兼容 + Mock）。"""

from app.rag.embeddings.base import (  # noqa: F401
    EmbeddingDimensionError,
    EmbeddingProvider,
)
from app.rag.embeddings.factory import (  # noqa: F401
    get_embedding_provider,
    set_embedding_override,
)
from app.rag.embeddings.mock import MockEmbeddingProvider  # noqa: F401
from app.rag.embeddings.openai_compatible import (  # noqa: F401
    OpenAICompatibleEmbeddingProvider,
)

__all__ = [
    "EmbeddingDimensionError",
    "EmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "MockEmbeddingProvider",
    "get_embedding_provider",
    "set_embedding_override",
]
