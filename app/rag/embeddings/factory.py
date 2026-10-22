"""嵌入模型工厂。

根据配置构建具体提供商；支持在测试 / 运行时通过 ``set_embedding_override``
注入 Mock 或自定义实现。当未配置 API Key 且处于开发环境时，自动降级为 Mock，
保证应用可离线启动与演示。
"""

import logging
from typing import TypedDict

from app.core.config import settings
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider

logger = logging.getLogger(__name__)

_override: EmbeddingProvider | None = None


class EmbeddingAvailability(TypedDict):
    """只读嵌入身份。不表示已连通，也不能当作检索质量证据。"""

    mode: str
    provider: str
    model: str
    dim: int
    is_mock: bool
    connectivity_verified: bool
    quality_evidence: bool


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
        timeout=settings.EMBEDDING_TIMEOUT_SECONDS,
        batch_size=settings.EMBEDDING_BATCH_SIZE,
    )


def embedding_availability() -> EmbeddingAvailability:
    """报告当前嵌入配置或注入实例的身份。

    不构造真实提供商、不调用 embed、不探测网络，也不返回密钥或服务地址。
    ``connectivity_verified`` 与 ``quality_evidence`` 恒为 False：本函数只能证明
    进程里看到的身份，不能证明检索质量或线上连通。
    """
    if _override is not None:
        return _availability_from_provider(_override, mode="override")

    provider = settings.EMBEDDING_PROVIDER.strip().lower()
    if provider == "mock" or (not settings.EMBEDDING_API_KEY and settings.is_development):
        return _availability(
            mode="mock",
            provider="mock",
            model="mock-embedding",
            dim=settings.EMBEDDING_DIM,
            is_mock=True,
        )
    if not settings.EMBEDDING_API_KEY:
        return _availability(
            mode="unavailable",
            provider=provider or "unknown",
            model=settings.EMBEDDING_MODEL,
            dim=settings.EMBEDDING_DIM,
            is_mock=False,
        )
    return _availability(
        mode="configured",
        provider=provider or "unknown",
        model=settings.EMBEDDING_MODEL,
        dim=settings.EMBEDDING_DIM,
        is_mock=False,
    )


def _availability(
    *,
    mode: str,
    provider: str,
    model: str,
    dim: int,
    is_mock: bool,
) -> EmbeddingAvailability:
    return {
        "mode": mode,
        "provider": provider,
        "model": model,
        "dim": dim,
        "is_mock": is_mock,
        "connectivity_verified": False,
        "quality_evidence": False,
    }


def _availability_from_provider(
    provider: EmbeddingProvider,
    *,
    mode: str,
) -> EmbeddingAvailability:
    if isinstance(provider, MockEmbeddingProvider):
        return _availability(
            mode=mode,
            provider="mock",
            model=provider.model,
            dim=provider.dim,
            is_mock=True,
        )
    if isinstance(provider, OpenAICompatibleEmbeddingProvider):
        return _availability(
            mode=mode,
            provider="openai_compatible",
            model=provider.model,
            dim=provider.dim,
            is_mock=False,
        )
    return _availability(
        mode=mode,
        provider="unknown",
        model=provider.model if provider.model else "unknown",
        dim=provider.dim,
        is_mock=False,
    )
