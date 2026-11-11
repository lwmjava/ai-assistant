"""Optional cheaper-tier generation for already-decided simple routes.

The switch is off by default. Selection uses the explicit route kind, not
intent confidence, query length, or any other difficulty proxy.
"""

from __future__ import annotations

from app.agents.route import ChatRoute, RouteKind
from app.core.config import settings

# Routes that keep the default chat-tier generation model.
_CHAT_TIER_KINDS = frozenset({
    RouteKind.TOOLS,
    RouteKind.RAG,
    RouteKind.MULTI,
    RouteKind.COMBINED,
})


def model_tier_routing_enabled() -> bool:
    """Return whether the optional generation-tier experiment is on."""
    return bool(getattr(settings, "LLM_MODEL_TIER_ROUTING_ENABLED", False))


def select_generation_task(route: ChatRoute | None) -> str:
    """Choose the factory task used for answer generation.

    When the experiment is off, or the route is missing or not simple, this
    returns ``chat``. Simple routes may use the already configured ``intent``
    profile only while the experiment switch is on.
    """
    if not model_tier_routing_enabled() or route is None:
        return "chat"
    if route.kind in _CHAT_TIER_KINDS:
        return "chat"
    return "intent"
