"""RAG-028：模型能力契约与全部调用预算。

不连真实模型。HTTP 走 ``httpx.MockTransport`` 并计数，
被拦下的调用必须断言「一次请求都没发出」——护栏的价值全在发请求之前。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import types
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlmodel import Session

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.tools.base import Tool, ToolRegistry
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider
from app.llm.budget import (
    BUDGET_EXCEEDED_MESSAGE,
    ContextBudgetError,
    check_generation_payload,
    estimate_payload_tokens,
)
from app.llm.capabilities import (
    GenerationCapability,
    declared_capability,
    normalize_deployment,
    resolve_effective_capability,
    resolve_generation_capability,
)
from app.llm.counters import (
    OFFICIAL_METHOD,
    UTF8_METHOD,
    PayloadUncountableError,
    estimate_messages_tokens,
    official_counter_for,
    utf8_byte_count,
)
from app.llm.factory import get_llm_provider, set_llm_provider_override
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.routing import FallbackChain, is_failover_error
from app.models.user import User
from app.rag.ocr import openai_vision
from app.rag.ocr.base import OcrProviderError
from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider
from app.services.chat_service import ChatService

_TIKTOKEN_INSTALLED = importlib.util.find_spec("tiktoken") is not None
_OK = "完成"


# ── 夹具与辅助 ────────────────────────────────────
@pytest.fixture(autouse=True)
def _clean_budget_settings(monkeypatch: pytest.MonkeyPatch):
    """钉住预算相关配置，避免本机 .env 的残留值影响判定。"""
    monkeypatch.setattr(settings, "LLM_CAPABILITY_GUARD_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED", "")
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED_SOURCE", "")
    monkeypatch.setattr(settings, "LLM_BUDGET_SAFETY_MARGIN", 512)
    monkeypatch.setattr(settings, "LLM_OUTPUT_RESERVE_TOKENS", 2048)
    # 建链告警按 (profile, model) 进程级去重，用例之间必须各起一套。
    from app.llm import factory

    monkeypatch.setattr(factory, "_UNAPPROVED_WARNED", set())
    yield


class _Recorder:
    """记录真实发出去的 HTTP 请求。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    @property
    def count(self) -> int:
        return len(self.requests)


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    script: list[str] | None = None,
    *,
    sync: bool = False,
) -> None:
    """让 provider 内部新建的 httpx client 全部走 MockTransport 并计数。

    ``script`` 给出第 n 次请求要返回的正文，超出长度时复用最后一条。
    ``sync`` 同时盖住同步的 ``httpx.Client``（云 OCR 的同步入口用它）。
    """
    real_async = httpx.AsyncClient
    real_sync = httpx.Client

    def content_for() -> str:
        if not script:
            return _OK
        return script[min(len(recorder.requests) - 1, len(script) - 1)]

    def respond(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        body = json.loads(request.content or b"{}")
        text = content_for()
        if body.get("stream"):
            chunk = json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)
            return httpx.Response(
                200, content=f"data: {chunk}\n\ndata: [DONE]\n\n".encode()
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    transport = httpx.MockTransport(respond)

    def async_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async(*args, **kwargs)

    def sync_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_sync(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", async_factory)
    if sync:
        monkeypatch.setattr(httpx, "Client", sync_factory)


def _provider(
    model: str = "gpt-4o-mini",
    base_url: str = "https://api.openai.com/v1",
    capability: GenerationCapability | None = None,
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url=base_url, api_key="sk-test", model=model, capability=capability
    )


def _user(content: str = "你好") -> ChatMessage:
    return ChatMessage(role=ChatRole.USER, content=content)


async def _drain(provider: LLMProvider, messages: list[ChatMessage], options=None) -> list[str]:
    return [chunk async for chunk in provider.stream_chat(messages, options)]


class _FixedAnswer(LLMProvider):
    """固定返回一句话的提供商，用来顶替不需要计数的那一跳（如意图分流）。"""

    def __init__(self, answer: str, model: str = "fixed") -> None:
        self.model = model
        self.answer = answer

    async def chat(self, messages: list[ChatMessage], options: LLMOptions | None = None) -> str:
        return self.answer

    async def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        yield self.answer


class _BudgetBlocked(LLMProvider):
    """任何调用都触发预算护栏的假提供商。"""

    model = "lab-model"

    async def chat(self, messages: list[ChatMessage], options: LLMOptions | None = None) -> str:
        raise ContextBudgetError(
            "context_window_exceeded",
            payload_tokens=99_999,
            reserved_output=2048,
            margin=512,
            limit=4000,
        )

    async def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        raise ContextBudgetError(
            "context_window_exceeded",
            payload_tokens=99_999,
            reserved_output=2048,
            margin=512,
            limit=4000,
        )
        yield ""  # pragma: no cover — 让它成为生成器，行为与真实实现一致


# ── 1. 内置已核对条目 ─────────────────────────────
def test_builtin_openai_gpt4o_mini_is_verified() -> None:
    cap = resolve_generation_capability("https://api.openai.com/v1", "gpt-4o-mini")
    assert cap is not None
    assert cap.deployment == "api.openai.com/v1"
    assert cap.context_window == 128_000
    assert cap.max_output_tokens == 16_384
    assert cap.verified_on == "2026-10-07"
    assert "platform.openai.com" in cap.source


@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-pro"])
def test_builtin_deepseek_models_are_verified(model: str) -> None:
    cap = resolve_generation_capability("https://api.deepseek.com/v1", model)
    assert cap is not None
    assert cap.context_window == 1_000_000
    assert cap.max_output_tokens == 384_000
    assert cap.verified_on == "2026-10-07"
    assert "api-docs.deepseek.com" in cap.source


def test_deployment_normalization_ignores_case_slash_and_credentials() -> None:
    assert normalize_deployment("https://API.OpenAI.com/v1/") == "api.openai.com/v1"
    assert normalize_deployment("https://user:pw@api.openai.com/v1") == "api.openai.com/v1"
    assert resolve_generation_capability("https://api.openai.com/v1/", "gpt-4o-mini") is not None


def test_same_model_on_another_deployment_is_another_identity() -> None:
    """模型名相同不等于窗口相同：换部署就是换身份，不能借。"""
    assert resolve_generation_capability("https://gateway.internal/v1", "gpt-4o-mini") is None
    assert resolve_effective_capability("https://gateway.internal/v1", "gpt-4o-mini") is None


def test_capability_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError):
        GenerationCapability(
            deployment="d",
            model="m",
            context_window=0,
            max_output_tokens=16,
            counter=estimate_messages_tokens,
            counting_method=UTF8_METHOD,
            safety_margin=0,
            source="s",
            verified_on="v",
            version="ver",
        )


# ── 2/3. 未知模型与运营者声明 ─────────────────────
def test_unknown_model_is_blocked_without_sending_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="totally-unknown-model")

    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(provider.chat([_user()]))

    assert caught.value.reason == "capability_unverified"
    assert recorder.count == 0


def test_operator_declaration_allows_unknown_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """声明写成 部署!模型=窗口:输出，只在这个部署上生效。"""
    monkeypatch.setattr(
        settings, "LLM_CAPABILITY_DECLARED", "gateway.internal/v1!mystery-model=32768:4096"
    )
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED_SOURCE", "运营者压测报告 2026-10-07")
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="mystery-model", base_url="https://gateway.internal/v1")

    assert asyncio.run(provider.chat([_user()])) == _OK
    assert recorder.count == 1

    cap = declared_capability("mystery-model", "https://gateway.internal/v1")
    assert cap is not None
    assert (cap.context_window, cap.max_output_tokens) == (32_768, 4096)
    assert cap.deployment == "gateway.internal/v1"
    assert cap.counting_method == "operator-declared"
    assert cap.verified_on == "operator-declared"


def test_declaration_does_not_leak_to_another_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """为 gateway-a 声明一次，不得让 evil-gateway 上的同名模型也拿到窗口。"""
    monkeypatch.setattr(
        settings, "LLM_CAPABILITY_DECLARED", "gateway-a.example/v1!shared-model=32768:4096"
    )
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED_SOURCE", "运营者压测报告")
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="shared-model", base_url="https://evil-gateway.example/v1")

    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(provider.chat([_user()]))

    assert caught.value.reason == "capability_unverified"
    assert declared_capability("shared-model", "https://evil-gateway.example/v1") is None
    assert recorder.count == 0


def test_declaration_without_deployment_binds_to_main_deployment_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """省略部署时只作用于 LLM_BASE_URL 对应的主部署。"""
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://main.example/v1")
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED", "home-model=8000:4096")
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED_SOURCE", "运营者压测报告")

    home = declared_capability("home-model", "https://main.example/v1")
    assert home is not None
    assert home.deployment == "main.example/v1"
    assert declared_capability("home-model", "https://intent.example/v1") is None

    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    intent_provider = _provider(model="home-model", base_url="https://intent.example/v1")
    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(intent_provider.chat([_user()]))
    assert caught.value.reason == "capability_unverified"
    assert recorder.count == 0


def test_declaration_without_source_is_still_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """只有声明没有依据来源 = 未批准：来源是为了能追溯，不是为了好看。"""
    monkeypatch.setattr(
        settings, "LLM_CAPABILITY_DECLARED", "gateway.internal/v1!mystery-model=32768:4096"
    )
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="mystery-model", base_url="https://gateway.internal/v1")

    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(provider.chat([_user()]))

    assert caught.value.reason == "capability_unverified"
    assert declared_capability("mystery-model", "https://gateway.internal/v1") is None
    assert recorder.count == 0


def test_declared_window_above_reasonable_bound_is_rejected(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """「数值由运营者给」不等于写多少都算：1e9 的窗口是把护栏关掉。"""
    monkeypatch.setattr(
        settings, "LLM_CAPABILITY_DECLARED", "gateway.internal/v1!huge=1000000000:999999999"
    )
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED_SOURCE", "拍脑袋填的")
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="huge", base_url="https://gateway.internal/v1")

    with caplog.at_level(logging.ERROR, logger="app.llm.capabilities"):
        assert declared_capability("huge", "https://gateway.internal/v1") is None
        with pytest.raises(ContextBudgetError) as caught:
            asyncio.run(provider.chat([_user("x" * 5_000_000)]))

    assert caught.value.reason == "capability_unverified"
    assert "llm_capability_declared_out_of_range" in caplog.text
    assert recorder.count == 0


def test_declared_output_above_window_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED", "gateway.internal/v1!odd=1000:999999")
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED_SOURCE", "填反了")
    assert declared_capability("odd", "https://gateway.internal/v1") is None


# ── 4/5. payload、输出预留与余量 ──────────────────
def _budget_messages(cap: GenerationCapability, overflow: int) -> list[ChatMessage]:
    """构造一组消息，使 payload + 预留 + 余量 相对窗口的差为 ``overflow``。"""
    reserve = settings.LLM_OUTPUT_RESERVE_TOKENS
    margin = settings.LLM_BUDGET_SAFETY_MARGIN + cap.safety_margin
    base = [
        ChatMessage(role=ChatRole.SYSTEM, content="系统"),
        ChatMessage(role=ChatRole.USER, content=""),
    ]
    payload = estimate_payload_tokens(cap, base)
    assert payload is not None
    room = cap.context_window - reserve - margin
    unit = " x" if cap.counting_method == OFFICIAL_METHOD else "x"
    messages = [
        base[0],
        ChatMessage(role=ChatRole.USER, content=unit * (room - payload + overflow)),
    ]
    assert estimate_payload_tokens(cap, messages) == room + overflow
    return messages


def test_payload_plus_reserve_plus_margin_over_window_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider()
    cap = resolve_generation_capability("https://api.openai.com/v1", "gpt-4o-mini")
    assert cap is not None
    messages = _budget_messages(cap, overflow=1)

    assert (
        check_generation_payload(
            cap, messages, settings.LLM_OUTPUT_RESERVE_TOKENS, settings.LLM_BUDGET_SAFETY_MARGIN
        )
        == "context_window_exceeded"
    )
    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(provider.chat(messages))

    assert caught.value.reason == "context_window_exceeded"
    assert recorder.count == 0


def test_payload_exactly_at_budget_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider()
    cap = resolve_generation_capability("https://api.openai.com/v1", "gpt-4o-mini")
    assert cap is not None

    assert asyncio.run(provider.chat(_budget_messages(cap, overflow=0))) == _OK
    assert recorder.count == 1


def test_reserved_output_above_max_output_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider()

    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(provider.chat([_user()], LLMOptions(max_tokens=32_768)))

    assert caught.value.reason == "output_reserve_exceeded"
    assert recorder.count == 0


# ── 6. 工具结果与 critique 轮次被计入 ─────────────
def _small_capability(monkeypatch: pytest.MonkeyPatch) -> GenerationCapability:
    # 最大输出给足（> 默认输出预留 2048），这样被拦下只可能是窗口超限。
    # 窗口 4200：余量外的空间 1640，正好卡在「200 条短文按正文算装得下、
    # 加上框架开销装不下」之间；最大输出 4096 大于默认输出预留 2048，
    # 这样被拦下只可能是窗口超限。
    monkeypatch.setattr(
        settings, "LLM_CAPABILITY_DECLARED", "gateway.internal/v1!lab-model=4200:4096"
    )
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED_SOURCE", "测试用小窗口声明")
    cap = declared_capability("lab-model", "https://gateway.internal/v1")
    assert cap is not None
    return cap


def test_tool_results_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    _small_capability(monkeypatch)
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="lab-model", base_url="https://gateway.internal/v1")
    base = [
        ChatMessage(role=ChatRole.SYSTEM, content="系统"),
        _user("查一下库存"),
    ]

    # 没有工具返回时放行，证明拦下不是因为基础消息本身超限。
    assert asyncio.run(provider.chat(base)) == _OK
    assert recorder.count == 1

    with_tool = base + [
        ChatMessage(role=ChatRole.ASSISTANT, content="## 已调用工具结果\n" + "D" * 5000)
    ]
    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(provider.chat(with_tool))

    assert caught.value.reason == "context_window_exceeded"
    assert recorder.count == 1  # 被拦下那次没有发出请求


def test_critique_round_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    _small_capability(monkeypatch)
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="lab-model", base_url="https://gateway.internal/v1")
    base = [
        ChatMessage(role=ChatRole.SYSTEM, content="系统"),
        _user("写一段答复"),
    ]
    assert asyncio.run(provider.chat(base)) == _OK

    critique = base + [
        ChatMessage(role=ChatRole.USER, content="## 审查意见\n" + "C" * 5000)
    ]
    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(provider.chat(critique))

    assert caught.value.reason == "context_window_exceeded"
    assert recorder.count == 1


# ── 7. 流式同样被 Guard ───────────────────────────
def test_stream_path_is_guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="totally-unknown-model")

    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(_drain(provider, [_user()]))

    assert caught.value.reason == "capability_unverified"
    assert recorder.count == 0


# ── 8. 护栏关闭时行为不变 ─────────────────────────
def test_guard_disabled_keeps_previous_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_CAPABILITY_GUARD_ENABLED", False)
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="totally-unknown-model")

    assert asyncio.run(provider.chat([_user()])) == _OK
    assert asyncio.run(_drain(provider, [_user()])) == [_OK]
    assert recorder.count == 2


# ── 9. 调用方返回固定提示，不含异常原文 ───────────
def test_pipeline_returns_fixed_message_without_exception_text() -> None:
    state = asyncio.run(AgentPipeline(_BudgetBlocked()).run(AgentState(user_input="你好")))

    assert state.answer == BUDGET_EXCEEDED_MESSAGE
    assert "context_window_exceeded" not in state.answer
    assert "99999" not in state.answer
    assert "lab-model" not in state.answer


def test_chat_service_returns_fixed_message_without_exception_text() -> None:
    with Session(engine) as session:
        user = User(
            id="rag028-user",
            tenant_id="rag028-tenant",
            username="rag028",
            hashed_password="",
            role=Role.MEMBER.value,
            token_version=0,
            is_active=True,
        )
        session.add(user)
        session.commit()
        service = ChatService(_BudgetBlocked())
        _conv, answer = asyncio.run(service.chat(session, user, "你好"))

    assert answer == BUDGET_EXCEEDED_MESSAGE
    assert "context_window_exceeded" not in answer
    assert "99999" not in answer


# ── 10. 计数方法被如实报告 ────────────────────────
def test_counting_method_is_reported_in_metadata() -> None:
    cap = resolve_generation_capability("https://api.openai.com/v1", "gpt-4o-mini")
    assert cap is not None
    assert cap.counting_method in {UTF8_METHOD, OFFICIAL_METHOD}
    assert cap.metadata()["counting_method"] == cap.counting_method
    assert cap.counting_method in cap.describe()


def test_official_counter_is_not_claimed_for_other_vendors() -> None:
    """别家的 token 不能用 OpenAI 的 tokenizer 数——那是伪造精度。

    注意：tiktoken 未安装时这条断言是空跑的（无论怎样都返回 None）。
    真正让这条规则生效的断言在下面两条（注入假 tiktoken 之后）。
    """
    assert official_counter_for("api.deepseek.com/v1", "deepseek-flash") is None
    assert official_counter_for("gateway.internal/v1", "gpt-4o-mini") is None


class _FakeEncoding:
    """假 tokenizer：每 4 个字符 1 个 token + 1，便于断言「确实用了它」。"""

    def encode(self, text: str) -> list[int]:
        return [1] * (len(text) // 4 + 1)


@pytest.fixture()
def _fake_tiktoken(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """把假 tiktoken 塞进 sys.modules，让官方计数器分支真正被执行一次。"""
    module = types.ModuleType("tiktoken")
    module.encoding_for_model = lambda _model: _FakeEncoding()  # type: ignore[attr-defined]
    module.get_encoding = lambda _name: _FakeEncoding()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tiktoken", module)
    return module


def test_official_counter_is_used_on_openai_official_deployment(
    _fake_tiktoken: types.ModuleType,
) -> None:
    counter = official_counter_for("api.openai.com/v1", "gpt-4o-mini")
    assert counter is not None

    messages = [_user("a" * 40)]
    # 假 tokenizer：40 字符 → 11，加消息框架 4 与回复引导 3 = 18；
    # 字节估算则是 40 + 7 = 47。两者不同，证明走的确实是官方计数器。
    assert counter(messages) == 18
    assert estimate_messages_tokens(messages) == 47

    cap = resolve_generation_capability("https://api.openai.com/v1", "gpt-4o-mini")
    assert cap is not None
    assert cap.counting_method == OFFICIAL_METHOD


def test_official_counter_still_refused_for_other_vendors_even_when_installed(
    _fake_tiktoken: types.ModuleType,
) -> None:
    """装了 tiktoken 也不拿它数别家的 token——这条规则必须有可执行证据。"""
    assert official_counter_for("api.deepseek.com/v1", "deepseek-flash") is None
    assert official_counter_for("gateway.internal/v1", "gpt-4o-mini") is None

    deepseek = resolve_generation_capability("https://api.deepseek.com/v1", "deepseek-flash")
    assert deepseek is not None
    assert deepseek.counting_method == UTF8_METHOD


def test_fallback_counting_method_matches_installed_counter() -> None:
    cap = resolve_generation_capability("https://api.openai.com/v1", "gpt-4o-mini")
    assert cap is not None
    if _TIKTOKEN_INSTALLED:
        assert cap.counting_method == OFFICIAL_METHOD
    else:
        # 本机未安装 tiktoken（2026-10-07 实测），如实退回保守估算。
        assert cap.counting_method == UTF8_METHOD
        assert official_counter_for("api.openai.com/v1", "gpt-4o-mini") is None


def test_message_frame_overhead_is_counted() -> None:
    """只算内容不算框架会系统性偏低——工具轮次多时偏差被放大。"""
    empty = estimate_messages_tokens([])
    one = estimate_messages_tokens([_user("")])
    two = estimate_messages_tokens([_user(""), _user("")])
    assert two - one == one - empty > 0


def test_message_frame_overhead_can_block_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """正文按字节刚好装得下，但消息条数的框架开销把它顶出窗口。

    工具调用与自纠错恰好都是「条数多、每条不大」的形态，只算正文会漏掉这一类。
    """
    _small_capability(monkeypatch)
    cap = declared_capability("lab-model", "https://gateway.internal/v1")
    assert cap is not None
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="lab-model", base_url="https://gateway.internal/v1")

    room = cap.context_window - settings.LLM_OUTPUT_RESERVE_TOKENS - settings.LLM_BUDGET_SAFETY_MARGIN
    content = "y" * 7
    messages = [ChatMessage(role=ChatRole.USER, content=content) for _ in range(200)]
    assert 200 * len(content) < room  # 只算正文时确实装得下

    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(provider.chat(messages))

    assert caught.value.reason == "context_window_exceeded"
    assert recorder.count == 0


def test_utf8_byte_count_is_conservative_for_chinese() -> None:
    assert utf8_byte_count("知识库") == 9  # 3 字 × 3 字节 ≥ 实际 token 数


# ── 14. 计不出来就拒绝，不许计 0 ──────────────────
def test_multimodal_content_is_counted_as_json_not_repr() -> None:
    """多模态 content 按 repr 计数会把一张图算成几十字节——彻底的低估。"""
    image = "data:image/png;base64," + "A" * 5_000
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "请提取文字"},
            {"type": "image_url", "image_url": {"url": image}},
        ],
    }
    serialized = json.dumps(
        message["content"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    # 按 JSON（也就是真正外发的线格式）计，而不是按 Python repr 计。
    assert estimate_messages_tokens([message]) == len(serialized) + 7
    assert len(serialized) > len(image)  # 图片本体被算进去


def test_unserializable_content_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """序列化不了的 content 计不出来：不知道会不会超限，就不发。"""
    with pytest.raises(PayloadUncountableError):
        estimate_messages_tokens([{"role": "user", "content": [object()]}])

    cap = resolve_generation_capability("https://api.openai.com/v1", "gpt-4o-mini")
    assert cap is not None
    assert (
        check_generation_payload(cap, [{"role": "user", "content": [object()]}], 64, 512)
        == "payload_uncountable"
    )

    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(capability=cap)

    class _Raw:
        content = [object()]

    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(provider.chat([_Raw()]))  # type: ignore[list-item]

    assert caught.value.reason == "payload_uncountable"
    assert recorder.count == 0


@pytest.mark.parametrize("shape", ["text", "list", "dict"])
def test_unencodable_text_is_rejected(monkeypatch: pytest.MonkeyPatch, shape: str) -> None:
    """不可编码字符被计成 0 字节等于宣布任何窗口都装得下。"""
    broken = "\ud800" * 10
    with pytest.raises(PayloadUncountableError):
        utf8_byte_count(broken)

    cap = resolve_generation_capability("https://api.openai.com/v1", "gpt-4o-mini")
    assert cap is not None
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(capability=cap)

    class _Raw:
        content = broken if shape == "text" else ([broken] if shape == "list" else {"text": broken})

    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(provider.chat([_Raw()]))  # type: ignore[list-item]

    assert caught.value.reason == "payload_uncountable"
    assert recorder.count == 0


# ── 11. 建链时把「没有批准配置」说出来 ────────────
def test_factory_warns_when_capability_is_unapproved(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """运维升级后不能只看到「对话只剩固定提示」而找不到原因。"""
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-chat")
    monkeypatch.setattr(settings, "LLM_DEFAULT_MODEL", "deepseek-chat")
    set_llm_provider_override(None)

    with caplog.at_level(logging.WARNING, logger="app.llm.factory"):
        get_llm_provider("chat")

    assert "llm_capability_unapproved" in caplog.text
    assert "profile=chat" in caplog.text
    assert "deepseek-chat" in caplog.text
    assert "LLM_CAPABILITY_DECLARED_SOURCE" in caplog.text


def test_factory_warns_once_per_profile_and_model(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """同一 (profile, model) 只提示一次：ChatService.llm 是 property，每次访问都建链。"""
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-chat")
    monkeypatch.setattr(settings, "LLM_DEFAULT_MODEL", "deepseek-chat")
    set_llm_provider_override(None)

    with caplog.at_level(logging.WARNING, logger="app.llm.factory"):
        for _ in range(5):
            get_llm_provider("chat")

    assert caplog.text.count("llm_capability_unapproved") == 1


def test_factory_stays_silent_when_capability_is_approved(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-chat")
    monkeypatch.setattr(settings, "LLM_DEFAULT_MODEL", "gpt-4o-mini")
    set_llm_provider_override(None)

    with caplog.at_level(logging.WARNING, logger="app.llm.factory"):
        get_llm_provider("chat")

    assert "llm_capability_unapproved" not in caplog.text


# ── 12. 每一跳按自己的窗口判定（端到端）───────────
def test_each_profile_is_judged_by_its_own_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一份 payload：chat 走已核对大窗口放行，intent 走声明的小窗口被拦。"""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-chat")
    monkeypatch.setattr(settings, "LLM_DEFAULT_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(settings, "LLM_CHAT_FALLBACK_CHAIN", "chat")
    monkeypatch.setattr(settings, "LLM_INTENT_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_INTENT_BASE_URL", "https://intent.example/v1")
    monkeypatch.setattr(settings, "LLM_INTENT_API_KEY", "sk-intent")
    monkeypatch.setattr(settings, "LLM_INTENT_MODEL", "intent-small")
    monkeypatch.setattr(settings, "LLM_INTENT_FALLBACK_CHAIN", "intent")
    monkeypatch.setattr(settings, "LLM_FALLBACK_API_KEY", "")
    monkeypatch.setattr(
        settings, "LLM_CAPABILITY_DECLARED", "intent.example/v1!intent-small=1000:100"
    )
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED_SOURCE", "测试用小窗口声明")
    set_llm_provider_override(None)

    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    # 640 字节上下：装得进 gpt-4o-mini 的 128000，装不进 intent-small 的 1000。
    messages = [_user("查一下这个月的退货政策" + "。" * 200)]
    options = LLMOptions(max_tokens=64)

    chat = get_llm_provider("chat")
    intent = get_llm_provider("intent")

    assert asyncio.run(chat.chat(messages, options)) == _OK
    assert recorder.count == 1

    with pytest.raises(ContextBudgetError) as caught:
        asyncio.run(intent.chat(messages, options))

    assert caught.value.reason == "context_window_exceeded"
    assert recorder.count == 1  # intent 那一跳一次请求都没发


# ── 13. 工具循环端到端：工具返回把下一跳顶出窗口 ───
def test_tool_result_blocks_the_next_round_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "LLM_CAPABILITY_DECLARED", "gateway.internal/v1!tool-model=20000:4096"
    )
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED_SOURCE", "测试用工具循环声明")
    recorder = _Recorder()
    big_text = "X" * 30_000
    tool_call = '<tool_call>{"name": "big_tool", "arguments": {}}</tool_call>'
    # 理解 → 规划 → 行动（产出工具调用）；第 4 次是收尾，应当被护栏拦下。
    _patch_transport(monkeypatch, recorder, script=["意图：查询", "步骤：调用工具", tool_call])

    provider = _provider(model="tool-model", base_url="https://gateway.internal/v1")
    tool = Tool(
        name="big_tool",
        description="返回一大段文本",
        parameters={},
        func=lambda _arguments: big_text,
    )
    pipeline = AgentPipeline(
        provider,
        options=LLMOptions(max_tokens=1024),
        tools=ToolRegistry([tool]),
        intent_llm=_FixedAnswer("YES"),
    )
    pipeline.max_tool_rounds = 1

    state = asyncio.run(pipeline.run(AgentState(user_input="调用工具查一下")))

    assert state.tool_results and big_text in state.tool_results[0]  # 工具真的跑过
    assert state.answer == BUDGET_EXCEEDED_MESSAGE
    assert "context_window_exceeded" not in state.answer
    assert recorder.count == 3  # 理解、规划、行动；收尾那一跳没发出


# ── 兜底链不把预算错误当成可恢复故障 ──────────────
def test_budget_error_is_not_a_failover_error(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    chain = FallbackChain(
        "chat",
        [
            ("chat", _provider(model="unknown-a")),
            ("fallback", _provider(model="unknown-b")),
        ],
    )

    budget_error = ContextBudgetError(
        "capability_unverified", payload_tokens=1, reserved_output=1, margin=1, limit=0
    )
    assert is_failover_error(budget_error) is False
    with pytest.raises(ContextBudgetError):
        asyncio.run(chain.chat([_user()]))

    assert recorder.count == 0


# ── 15. Supervisor 编排不把预算超限说成「请稍后重试」──
def test_supervisor_reports_budget_message_not_retry_hint() -> None:
    """langgraph 编排下预算超限必须说「请缩短输入」，说「请稍后重试」是诱导重试。"""
    pytest.importorskip("langgraph")
    from app.agents.supervisor import SupervisorGraph

    graph = SupervisorGraph(_BudgetBlocked())
    state = asyncio.run(graph.run(AgentState(user_input="你好")))

    assert state.answer == BUDGET_EXCEEDED_MESSAGE
    assert "请稍后重试" not in state.answer


# ── 16. 云 OCR 视觉调用同样在护栏内 ────────────────
def _ocr_provider(
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    model: str,
    page_bytes: bytes = b"fake",
) -> OpenAiVisionOcrProvider:
    """构造云 OCR provider：渲染页被打桩，HTTP 走 MockTransport 计数。"""
    monkeypatch.setattr(openai_vision, "_render_pdf_pages", lambda _data: [page_bytes])
    _patch_transport(monkeypatch, recorder, sync=True)
    return OpenAiVisionOcrProvider(
        base_url="https://vision.example/v1",
        api_key="sk-ocr",
        model=model,
        timeout_seconds=5.0,
    )


def test_cloud_ocr_blocks_unknown_model_without_http(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    provider = _ocr_provider(monkeypatch, recorder, "unknown-vision-model")

    with pytest.raises(OcrProviderError) as caught:
        provider.extract_pdf_text(b"pdf-bytes")

    assert caught.value.error_code == "ocr_budget_exceeded"
    assert recorder.count == 0


def test_cloud_ocr_blocks_oversized_image_without_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "LLM_CAPABILITY_DECLARED", "vision.example/v1!vision-model=6000:4096"
    )
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED_SOURCE", "测试用视觉声明")
    recorder = _Recorder()
    # 10 KB 图片 → base64 约 13.4 KB，远超 6000-2048-512=3440 的余量。
    provider = _ocr_provider(
        monkeypatch, recorder, "vision-model", page_bytes=b"x" * 10_000
    )

    with pytest.raises(OcrProviderError) as caught:
        asyncio.run(provider.aextract_pdf_text(b"pdf-bytes"))

    assert caught.value.error_code == "ocr_budget_exceeded"
    assert recorder.count == 0
