"""任务类型路由与兜底链。不连接真实模型。"""

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx
import pytest

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.tools.base import ToolRegistry
from app.core.config import settings
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider
from app.llm.factory import get_llm_provider, llm_availability, set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.routing import UNAVAILABLE_MESSAGE, FallbackChain, LLMUnavailableError
from app.services.chat_service import ChatService

_SECRET = "sk-test-secret"


@pytest.fixture(autouse=True)
def _blank_llm(monkeypatch: pytest.MonkeyPatch):
    """清掉本机 .env 里的密钥，避免测试读到真实配置。"""
    set_llm_provider_override(None)
    monkeypatch.setattr(settings, "ENV", "development")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://chat.example/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_DEFAULT_MODEL", "chat-model")
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_FALLBACK_BASE_URL", "https://fallback.example/v1")
    monkeypatch.setattr(settings, "LLM_FALLBACK_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_MODEL", "fallback-model")
    monkeypatch.setattr(settings, "LLM_INTENT_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_INTENT_BASE_URL", "https://intent.example/v1")
    monkeypatch.setattr(settings, "LLM_INTENT_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_INTENT_MODEL", "intent-model")
    monkeypatch.setattr(settings, "LLM_CHAT_FALLBACK_CHAIN", "chat,fallback")
    monkeypatch.setattr(settings, "LLM_INTENT_FALLBACK_CHAIN", "intent,chat,fallback")
    yield
    set_llm_provider_override(None)


class _Scripted(LLMProvider):
    def __init__(
        self,
        model: str,
        *,
        result: str = "ok",
        error: BaseException | None = None,
        fail_after_chunk: bool = False,
    ) -> None:
        self.model = model
        self.result = result
        self.error = error
        self.fail_after_chunk = fail_after_chunk
        self.calls = 0

    async def chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    async def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        self.calls += 1
        if self.fail_after_chunk:
            yield "已出字"
            raise self.error or httpx.TimeoutException("timeout")
        if self.error is not None:
            raise self.error
        yield self.result


def _status(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("status", request=request, response=response)


def _user() -> list[ChatMessage]:
    return [ChatMessage(role=ChatRole.USER, content="你好")]


def test_primary_only_returns_compatible_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-chat")
    provider = get_llm_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "chat-model"
    assert provider.base_url == "https://chat.example/v1"


def test_empty_intent_uses_chat_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-chat")
    intent = get_llm_provider("intent")
    assert isinstance(intent, OpenAICompatibleProvider)
    assert intent.model == "chat-model"
    assert intent.base_url == "https://chat.example/v1"


def test_intent_config_is_separate_from_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-chat")
    monkeypatch.setattr(settings, "LLM_INTENT_API_KEY", "sk-intent")
    intent = get_llm_provider("intent")
    chat = get_llm_provider("chat")
    assert isinstance(intent, FallbackChain)
    assert [name for name, _ in intent.hops] == ["intent", "chat"]
    assert intent.hops[0][1].model == "intent-model"
    assert isinstance(chat, OpenAICompatibleProvider)
    assert chat.model == "chat-model"


def test_pipeline_intent_and_chat_use_different_clients() -> None:
    intent = _Scripted("intent-model", result="NO")
    chat = _Scripted("chat-model", result="对话正文")
    pipeline = AgentPipeline(chat, intent_llm=intent)
    state = AgentState(user_input="你好")

    asyncio.run(pipeline._needs_plan(state))
    asyncio.run(pipeline._stage("系统", "_build_understand", state))

    assert intent.calls == 1
    assert chat.calls == 1


def test_pipeline_without_intent_client_uses_the_same_one() -> None:
    chat = _Scripted("chat-model", result="YES")
    pipeline = AgentPipeline(chat)
    asyncio.run(pipeline._needs_plan(AgentState(user_input="你好")))
    assert chat.calls == 1
    assert pipeline.intent_llm is chat


def test_fallback_on_timeout_and_not_on_400() -> None:
    primary = _Scripted("chat-model", error=httpx.TimeoutException("timeout"))
    fallback = _Scripted("fallback-model", result="下一家")
    chain = FallbackChain("chat", [("chat", primary), ("fallback", fallback)])
    assert asyncio.run(chain.chat(_user())) == "下一家"
    assert fallback.calls == 1

    server = _Scripted("chat-model", error=_status(502))
    recovered = _Scripted("fallback-model", result="五零二")
    chain502 = FallbackChain("chat", [("chat", server), ("fallback", recovered)])
    assert asyncio.run(chain502.chat(_user())) == "五零二"

    denied = _Scripted("chat-model", error=_status(400))
    other = _Scripted("fallback-model", result="不该出现")
    blocked = FallbackChain("chat", [("chat", denied), ("fallback", other)])
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(blocked.chat(_user()))
    assert other.calls == 0


def test_stream_switches_only_before_first_token() -> None:
    early = _Scripted("chat-model", error=httpx.TimeoutException("timeout"))
    nxt = _Scripted("fallback-model", result="第二家")
    chain = FallbackChain("chat", [("chat", early), ("fallback", nxt)])
    text = "".join(asyncio.run(_collect(chain)))
    assert text == "第二家"

    partial = _Scripted("chat-model", fail_after_chunk=True)
    later = _Scripted("fallback-model", result="不该拼接")
    broken = FallbackChain("chat", [("chat", partial), ("fallback", later)])
    with pytest.raises(LLMUnavailableError):
        asyncio.run(_collect(broken))
    assert later.calls == 0


async def _collect(provider: LLMProvider) -> list[str]:
    return [chunk async for chunk in provider.stream_chat(_user())]


def test_all_failed_hides_secret(caplog: pytest.LogCaptureFixture) -> None:
    primary = _Scripted("chat-model", error=httpx.TimeoutException(_SECRET))
    fallback = _Scripted("fallback-model", error=httpx.ConnectError(_SECRET))
    chain = FallbackChain("chat", [("chat", primary), ("fallback", fallback)])
    with caplog.at_level(logging.WARNING):
        with pytest.raises(LLMUnavailableError) as caught:
            asyncio.run(chain.chat(_user()))
    assert str(caught.value) == UNAVAILABLE_MESSAGE
    assert _SECRET not in str(caught.value)
    assert _SECRET not in caplog.text


def test_development_without_key_is_mock() -> None:
    provider = get_llm_provider()
    assert isinstance(provider, MockLLMProvider)
    assert llm_availability() == "mock"


def test_production_without_key_fails_on_call_not_on_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENV", "production")
    provider = get_llm_provider()
    assert llm_availability() == "unavailable"
    with pytest.raises(LLMUnavailableError, match=UNAVAILABLE_MESSAGE):
        asyncio.run(provider.chat(_user()))


def test_fallback_key_alone_is_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_FALLBACK_API_KEY", "sk-fallback")
    assert llm_availability() == "real"
    provider = get_llm_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "fallback-model"


def test_explicit_mock_still_uses_real_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(settings, "LLM_API_KEY", _SECRET)
    monkeypatch.setattr(settings, "LLM_FALLBACK_API_KEY", "sk-fallback")
    provider = get_llm_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "fallback-model"
    assert provider.api_key == "sk-fallback"


def test_unknown_task_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知的模型用途"):
        get_llm_provider("plan")


def test_override_covers_chat_and_intent() -> None:
    sentinel = MockLLMProvider(model="override")
    set_llm_provider_override(sentinel)
    assert get_llm_provider("chat") is sentinel
    assert get_llm_provider("intent") is sentinel


def test_pipeline_shows_fixed_message_when_no_model() -> None:
    pipeline = AgentPipeline(_Scripted("x", error=LLMUnavailableError()))
    state = asyncio.run(pipeline.run(AgentState(user_input="你好")))
    assert state.answer == UNAVAILABLE_MESSAGE
    assert _SECRET not in state.answer


def test_injected_client_covers_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self")
    monkeypatch.setattr(settings, "MCP_ENABLED", False)
    injected = MockLLMProvider(model="injected")
    pipeline = ChatService(injected)._build_pipeline(None, ToolRegistry([]))
    assert pipeline.llm is injected
    assert pipeline.intent_llm is injected
