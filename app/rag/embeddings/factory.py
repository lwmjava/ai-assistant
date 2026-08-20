"""嵌入模型工厂。

根据配置构建具体提供商；支持在测试 / 运行时通过 ``set_embedding_override``
注入 Mock 或自定义实现。当未配置 API Key 且处于开发环境时，自动降级为 Mock，
保证应用可离线启动与演示。
"""

import logging

from app.core.config import settings
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider

logger = logging.getLogger(__name__)

_override: EmbeddingProvider | None = None


def set_embedding_override(provider: EmbeddingProvider | None) -> None:
    """覆盖全局嵌入提供商（测试或运行时注入）。传入 None 取消覆盖。"""
    global _override
    _override = provider


def get_embedding_provider() -> EmbeddingProvider:
    """返回当前生效的嵌入模型提供商。"""
    if _override is not None:
        return _override

    provider = settings.EMBEDDING_PROVIDER.strip().lower()
    if provider == "mock":
        return MockEmbeddingProvider(dim=settings.EMBEDDING_DIM)

    if not settings.EMBEDDING_API_KEY:
        if settings.is_development:
            logger.warning("未配置 EMBEDDING_API_KEY，开发环境自动降级为 Mock 嵌入。")
            return MockEmbeddingProvider(dim=settings.EMBEDDING_DIM)
        raise RuntimeError("生产环境必须配置 EMBEDDING_API_KEY，否则无法生成向量。")

    return OpenAICompatibleEmbeddingProvider(
        base_url=settings.EMBEDDING_BASE_URL,
        api_key=settings.EMBEDDING_API_KEY,
        model=settings.EMBEDDING_MODEL,
        dim=settings.EMBEDDING_DIM,
        timeout=settings.LLM_TIMEOUT,
    )
