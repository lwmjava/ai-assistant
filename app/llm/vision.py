"""Verified low-detail image input boundary; no inference or filesystem operations."""

from collections.abc import Sequence
from urllib.parse import urlsplit

from app.core.config import settings
from app.llm.base import ChatMessage, ChatRole, LLMProvider, user_message_content
from app.llm.budget import ContextBudgetError, enforce_generation_budget
from app.llm.capabilities import normalize_deployment, resolve_generation_capability


def visual_capability(base_url: str, model: str):
    try:
        parsed = urlsplit(base_url)
        deployment = normalize_deployment(base_url)
    except ValueError:
        raise ContextBudgetError("vision_capability_unverified", payload_tokens=-1,
                                 reserved_output=0, margin=0, limit=0) from None
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment
            or deployment not in {"api.openai.com", "api.openai.com/v1"}
            or model != "gpt-4o-mini"):
        raise ContextBudgetError("vision_capability_unverified", payload_tokens=-1,
                                 reserved_output=0, margin=0, limit=0)
    capability = resolve_generation_capability(base_url, model)
    if capability is None:
        raise ContextBudgetError("vision_capability_unverified", payload_tokens=-1,
                                 reserved_output=0, margin=0, limit=0)
    return capability


def has_image_inputs(messages) -> bool:
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        if isinstance(content, list):
            for part in content:
                kind = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
                if kind == "image_url":
                    return True
    return False


def validate_image_inputs(provider: LLMProvider, image_urls: Sequence[str], text: str = "") -> None:
    """Before message writes, verify every configured generation hop without sending data."""
    if not image_urls:
        return
    hops = getattr(provider, "hops", None)
    if isinstance(hops, list):
        if not hops:
            visual_capability("", "")
        for _, hop in hops:
            validate_image_inputs(hop, image_urls, text)
        return
    delegate = getattr(provider, "_delegate", None)
    if delegate is not None:
        validate_image_inputs(delegate, image_urls, text)
        return
    capability = visual_capability(getattr(provider, "base_url", ""), provider.model)
    messages = [ChatMessage(role=ChatRole.USER, content=user_message_content(text, image_urls))]
    enforce_generation_budget(capability, messages, settings.LLM_OUTPUT_RESERVE_TOKENS,
                              settings.LLM_BUDGET_SAFETY_MARGIN + capability.safety_margin)
