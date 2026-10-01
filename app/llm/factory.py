"""LLM 提供商工厂。

按用途取出一条链。本批只登记 chat 与 intent。密钥只从环境变量读取。
测试可通过 ``set_llm_provider_override`` 同时盖住两种用途。
"""

import logging

from app.core.config import settings
from app.llm.base import LLMProvider
from app.llm.mock import MockLLMProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.routing import FallbackChain

logger = logging.getLogger(__name__)

_override: LLMProvider | None = None

_TASK_SETTINGS = {
    "chat": "LLM_CHAT_FALLBACK_CHAIN",
    "intent": "LLM_INTENT_FALLBACK_CHAIN",
}
_KNOWN_PROFILES = {"chat", "intent", "fallback"}


def set_llm_provider_override(provider: LLMProvider | None) -> None:
    """覆盖全局提供商（测试或运行时注入）。传入 None 取消覆盖。"""
    global _override
    _override = provider


def get_llm_provider(task: str = "chat") -> LLMProvider:
    """返回指定用途的提供商。未知用途拒绝，不悄悄当成对话。"""
    if task not in _TASK_SETTINGS:
        raise ValueError(f"未知的模型用途：{task}")
    if _override is not None:
        return _override

    hops = _real_hops(task)
    if not hops:
        if settings.is_development:
            logger.warning("未配置可用的模型密钥，开发环境使用 Mock。")
            return MockLLMProvider()
        return FallbackChain(task, [])
    if len(hops) == 1:
        return hops[0][1]
    return FallbackChain(task, hops)


def llm_availability() -> str:
    """只读对话链的配置，判断有没有真实模型。不拨外网，不返回密钥、地址或模型名。"""
    if _real_hops("chat"):
        return "real"
    if settings.is_development:
        return "mock"
    return "unavailable"


def _chain_names(task: str) -> list[str]:
    raw = getattr(settings, _TASK_SETTINGS[task])
    seen: set[str] = set()
    names: list[str] = []
    for part in str(raw).split(","):
        name = part.strip().lower()
        if name not in _KNOWN_PROFILES or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _real_hops(task: str) -> list[tuple[str, LLMProvider]]:
    hops: list[tuple[str, LLMProvider]] = []
    for name in _chain_names(task):
        client = _client_for(name)
        if client is not None:
            hops.append((name, client))
    return hops


def _client_for(name: str) -> OpenAICompatibleProvider | None:
    """有密钥且不是 mock 才算一跳。没有密钥的意图或兜底直接跳过。"""
    provider, base_url, api_key, model = _fields(name)
    if provider == "mock" or not api_key.strip():
        return None
    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=settings.LLM_TIMEOUT,
    )


def _fields(name: str) -> tuple[str, str, str, str]:
    if name == "chat":
        return (
            settings.LLM_PROVIDER.strip().lower(),
            settings.LLM_BASE_URL,
            settings.LLM_API_KEY,
            settings.LLM_DEFAULT_MODEL,
        )
    if name == "intent":
        return (
            (settings.LLM_INTENT_PROVIDER or "openai").strip().lower(),
            settings.LLM_INTENT_BASE_URL or settings.LLM_BASE_URL,
            settings.LLM_INTENT_API_KEY,
            settings.LLM_INTENT_MODEL or settings.LLM_DEFAULT_MODEL,
        )
    return (
        (settings.LLM_FALLBACK_PROVIDER or "openai").strip().lower(),
        settings.LLM_FALLBACK_BASE_URL or settings.LLM_BASE_URL,
        settings.LLM_FALLBACK_API_KEY,
        settings.LLM_FALLBACK_MODEL or settings.LLM_DEFAULT_MODEL,
    )
