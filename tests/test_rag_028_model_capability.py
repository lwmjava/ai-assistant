"""RAG-028：模型能力契约与全部调用预算。

不连真实模型。HTTP 走 ``httpx.MockTransport`` 并计数，
被拦下的调用必须断言「一次请求都没发出」——护栏的价值全在发请求之前。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlmodel import Session

from app.agents.pipeline import AgentPipeline, AgentState
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider
from app.llm.budget import (
    BUDGET_EXCEEDED_MESSAGE,
    ContextBudgetExceeded,
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
    estimate_messages_tokens,
    official_counter_for,
    utf8_byte_count,
)
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.routing import FallbackChain, is_failover_error
from app.models.user import User
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
    yield


class _Recorder:
    """记录真实发出去的 HTTP 请求。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    @property
    def count(self) -> int:
        return len(self.requests)


def _patch_transport(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    """让 provider 内部新建的 AsyncClient 全部走 MockTransport 并计数。"""
    real_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        body = json.loads(request.content or b"{}")
        if body.get("stream"):
            chunk = json.dumps({"choices": [{"delta": {"content": _OK}}]}, ensure_ascii=False)
            return httpx.Response(
                200, content=f"data: {chunk}\n\ndata: [DONE]\n\n".encode("utf-8")
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": _OK}}]})

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(respond)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


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


class _BudgetBlocked(LLMProvider):
    """任何调用都触发预算护栏的假提供商。"""

    model = "lab-model"

    async def chat(self, messages: list[ChatMessage], options: LLMOptions | None = None) -> str:
        raise ContextBudgetExceeded(
            "context_window_exceeded",
            payload_tokens=99_999,
            reserved_output=2048,
            margin=512,
            limit=4000,
        )

    async def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        raise ContextBudgetExceeded(
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

    with pytest.raises(ContextBudgetExceeded) as caught:
        asyncio.run(provider.chat([_user()]))

    assert caught.value.reason == "capability_unverified"
    assert recorder.count == 0


def test_operator_declaration_allows_unknown_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED", "mystery-model=32768:4096")
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED_SOURCE", "运营者压测报告 2026-10-07")
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="mystery-model", base_url="https://gateway.internal/v1")

    assert asyncio.run(provider.chat([_user()])) == _OK
    assert recorder.count == 1

    cap = declared_capability("mystery-model")
    assert cap is not None
    assert (cap.context_window, cap.max_output_tokens) == (32_768, 4096)
    assert cap.counting_method == "operator-declared"
    assert cap.verified_on == "operator-declared"


def test_declaration_without_source_is_still_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """只有声明没有依据来源 = 未批准：来源是为了能追溯，不是为了好看。"""
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED", "mystery-model=32768:4096")
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="mystery-model", base_url="https://gateway.internal/v1")

    with pytest.raises(ContextBudgetExceeded) as caught:
        asyncio.run(provider.chat([_user()]))

    assert caught.value.reason == "capability_unverified"
    assert declared_capability("mystery-model") is None
    assert recorder.count == 0


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
    room = cap.context_window - reserve - margin
    return [
        base[0],
        ChatMessage(role=ChatRole.USER, content="x" * (room - payload + overflow)),
    ]


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
    with pytest.raises(ContextBudgetExceeded) as caught:
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

    with pytest.raises(ContextBudgetExceeded) as caught:
        asyncio.run(provider.chat([_user()], LLMOptions(max_tokens=32_768)))

    assert caught.value.reason == "output_reserve_exceeded"
    assert recorder.count == 0


# ── 6. 工具结果与 critique 轮次被计入 ─────────────
def _small_capability(monkeypatch: pytest.MonkeyPatch) -> GenerationCapability:
    # 最大输出给足（> 默认输出预留 2048），这样被拦下只可能是窗口超限。
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED", "lab-model=4000:4096")
    monkeypatch.setattr(settings, "LLM_CAPABILITY_DECLARED_SOURCE", "测试用小窗口声明")
    cap = declared_capability("lab-model")
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
    with pytest.raises(ContextBudgetExceeded) as caught:
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
    with pytest.raises(ContextBudgetExceeded) as caught:
        asyncio.run(provider.chat(critique))

    assert caught.value.reason == "context_window_exceeded"
    assert recorder.count == 1


# ── 7. 流式同样被 Guard ───────────────────────────
def test_stream_path_is_guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="totally-unknown-model")

    with pytest.raises(ContextBudgetExceeded) as caught:
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
    """别家的 token 不能用 OpenAI 的 tokenizer 数——那是伪造精度。"""
    assert official_counter_for("api.deepseek.com/v1", "deepseek-flash") is None
    assert official_counter_for("gateway.internal/v1", "gpt-4o-mini") is None


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
    cap = declared_capability("lab-model")
    assert cap is not None
    recorder = _Recorder()
    _patch_transport(monkeypatch, recorder)
    provider = _provider(model="lab-model", base_url="https://gateway.internal/v1")

    room = cap.context_window - settings.LLM_OUTPUT_RESERVE_TOKENS - settings.LLM_BUDGET_SAFETY_MARGIN
    content = "y" * 7
    messages = [ChatMessage(role=ChatRole.USER, content=content) for _ in range(200)]
    assert 200 * len(content) < room  # 只算正文时确实装得下

    with pytest.raises(ContextBudgetExceeded) as caught:
        asyncio.run(provider.chat(messages))

    assert caught.value.reason == "context_window_exceeded"
    assert recorder.count == 0


def test_utf8_byte_count_is_conservative_for_chinese() -> None:
    assert utf8_byte_count("知识库") == 9  # 3 字 × 3 字节 ≥ 实际 token 数


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

    assert is_failover_error(ContextBudgetExceeded("capability_unverified", payload_tokens=1, reserved_output=1, margin=1, limit=0)) is False
    with pytest.raises(ContextBudgetExceeded):
        asyncio.run(chain.chat([_user()]))

    assert recorder.count == 0
