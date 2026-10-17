"""熔断必须跨临时 provider 实例生效，且不污染其他配置身份。"""

import asyncio
import secrets
from types import SimpleNamespace

import httpx

from app.llm import factory, routing
from app.llm.base import ChatMessage, ChatRole, LLMProvider


class ScriptedProvider(LLMProvider):
    def __init__(self, identity, *, failure=False):
        self.model = identity
        self.base_url = "https://" + identity + ".invalid/v1"
        self.api_key = secrets.token_urlsafe(24)
        self.failure = failure
        self.calls = 0

    async def chat(self, messages, options=None):
        self.calls += 1
        if self.failure:
            raise httpx.ReadTimeout("synthetic timeout")
        return "fallback response"

    async def stream_chat(self, messages, options=None):
        yield await self.chat(messages, options)


def test_circuit_skips_failed_primary_across_factory_instances(monkeypatch):
    reset = getattr(routing, "reset_circuits", None)
    if reset:
        reset()
    factory.set_llm_provider_override(None)
    primary = ScriptedProvider("primary-" + secrets.token_hex(8), failure=True)
    fallback = ScriptedProvider("fallback-" + secrets.token_hex(8))
    monkeypatch.setattr(factory, "_real_hops", lambda task: [("chat", primary), ("fallback", fallback)])
    messages = [ChatMessage(role=ChatRole.USER, content="synthetic request")]
    for _ in range(4):
        provider = factory.get_llm_provider("chat")
        assert asyncio.run(provider.chat(messages)) == "fallback response"
    assert primary.calls == 3
    assert fallback.calls == 4


def test_registry_is_bounded_and_idle_configuration_entries_expire(monkeypatch):
    routing.reset_circuits()
    clock = [1000.0]
    monkeypatch.setattr(routing, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(routing, "MAX_CIRCUITS", 2)
    messages = [ChatMessage(role=ChatRole.USER, content="bounded circuit request")]
    for index in range(3):
        provider = ScriptedProvider("bounded-" + str(index))
        assert asyncio.run(routing.FallbackChain("chat", [("chat", provider)]).chat(messages)) == "fallback response"
    assert len(routing._circuits.states) == 2
    clock[0] += 601
    fresh = ScriptedProvider("fresh-configuration")
    assert asyncio.run(routing.FallbackChain("chat", [("chat", fresh)]).chat(messages)) == "fallback response"
    assert len(routing._circuits.states) == 1
    routing.reset_circuits()
