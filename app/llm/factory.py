"""LLM 提供商工厂。

按用途取出一条链。本批只登记 chat 与 intent。密钥只从环境变量读取。
测试可通过 ``set_llm_provider_override`` 同时盖住两种用途。
"""

import logging

from app.core.config import settings
from app.llm.base import LLMProvider
from app.llm.capabilities import resolve_effective_capability
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
    client = OpenAICompatibleProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=settings.LLM_TIMEOUT,
    )
    _warn_if_unapproved(name, base_url, model)
    return client


# 同一次进程里同一 (profile, model) 只提示一次。``ChatService.llm`` 是 property，
# 每次访问都重新建链，不去重的话一次请求会打出 4~6 条一模一样的 WARNING。
_UNAPPROVED_WARNED: set[tuple[str, str]] = set()


def _warn_if_unapproved(profile: str, base_url: str, model: str) -> None:
    """建链时把「这个模型没有批准配置」说出来。

    护栏默认开启且未知即拒绝，若不提示，运维升级后只会看到「对话只剩固定提示」
    而找不到原因。这里只带 profile 与 model：不带 base_url、不带密钥、
    不带任何请求内容。
    """
    if not settings.LLM_CAPABILITY_GUARD_ENABLED:
        return
    if resolve_effective_capability(base_url, model) is not None:
        return
    key = (profile, model)
    if key in _UNAPPROVED_WARNED:
        return
    _UNAPPROVED_WARNED.add(key)
    logger.warning(
        "llm_capability_unapproved profile=%s model=%s：该模型没有已核对或已声明的"
        "能力配置，护栏开启后调用会被拒绝。"
        "请登记 LLM_CAPABILITY_DECLARED 与 LLM_CAPABILITY_DECLARED_SOURCE。",
        profile,
        model,
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
