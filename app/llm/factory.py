"""LLM 提供商工厂。

根据配置构建具体提供商；支持在测试 / 运行时通过 ``set_llm_provider_override``
注入 Mock 或自定义实现。当未配置 API Key 且处于开发环境时，自动降级为 Mock，
保证应用可离线启动与演示。
"""

import logging

from app.core.config import settings
from app.llm.base import LLMProvider
from app.llm.mock import MockLLMProvider
from app.llm.openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger(__name__)

_override: LLMProvider | None = None


def set_llm_provider_override(provider: LLMProvider | None) -> None:
    """覆盖全局提供商（测试或运行时注入）。传入 None 取消覆盖。"""
    global _override
    _override = provider


def get_llm_provider() -> LLMProvider:
    """返回当前生效的 LLM 提供商。"""
    if _override is not None:
        return _override

    provider = settings.LLM_PROVIDER.strip().lower()
    if provider == "mock":
        return MockLLMProvider()

    if not settings.LLM_API_KEY:
        if settings.is_development:
            logger.warning("未配置 LLM_API_KEY，开发环境自动降级为 Mock 提供商。")
            return MockLLMProvider()
        raise RuntimeError("生产环境必须配置 LLM_API_KEY，否则无法连接大模型。")

    return OpenAICompatibleProvider(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_DEFAULT_MODEL,
        timeout=settings.LLM_TIMEOUT,
    )
