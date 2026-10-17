"""Successful HTTP status must not turn an empty or malformed reply into usable text."""

import asyncio

import httpx
import pytest

from app.core.config import settings
from app.llm.base import ChatMessage, ChatRole, LLMOptions
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.routing import is_failover_error


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(settings, "LLM_CAPABILITY_GUARD_ENABLED", False)
    monkeypatch.setattr(settings, "LLM_RESPONSE_CACHE_ENABLED", False)
    original = httpx.AsyncClient

    def configure(body):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=body)

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
            transport=httpx.MockTransport(handler), **kwargs))
        return requests

    return configure


def invoke():
    provider = OpenAICompatibleProvider("https://isolated.invalid/v1", "synthetic-response-key", "local-test")
    return asyncio.run(provider.chat([ChatMessage(role=ChatRole.USER, content="synthetic request")],
                                    LLMOptions(temperature=0, timeout=1)))


@pytest.mark.parametrize("content", ["", " ", "\n\t", None])
def test_empty_success_reply_is_an_explicit_response_failure(transport, content):
    requests = transport({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})
    with pytest.raises(RuntimeError, match="^llm_empty_response$") as error:
        invoke()
    assert error.value.error_code == "llm_empty_response"
    assert error.value.finish_reason == "stop"
    assert error.value.native_tool_calls is False
    assert len(requests) == 1
    assert is_failover_error(error.value) is False


@pytest.mark.parametrize("body", [None, {}, {"choices": []}, {"choices": [None]},
                                 {"choices": [{"message": []}]},
                                 {"choices": [{"message": {"content": False}}]},
                                 {"choices": [{"message": {"content": {"private": "synthetic-only"}}}]}])
def test_malformed_success_reply_has_fixed_error(transport, body):
    requests = transport(body)
    with pytest.raises(RuntimeError, match="^llm_invalid_response$"):
        invoke()
    assert len(requests) == 1


@pytest.mark.parametrize("reason,expected", [("length", "length"), ("tool_calls", "tool_calls"),
                                          ("synthetic-private-reason", "other"), (None, "missing")])
def test_empty_failure_exposes_only_safe_response_metadata(transport, reason, expected, caplog):
    requests = transport({"choices": [{"message": {"content": "", "reasoning_content": "private-thought",
                                                   "tool_calls": [{"function": {"arguments": "private-body"}}]},
                                       "finish_reason": reason}]})
    with pytest.raises(RuntimeError, match="^llm_empty_response$") as error:
        invoke()
    assert error.value.finish_reason == expected
    assert error.value.native_tool_calls is True
    assert all(value not in str(error.value) + caplog.text for value in (
        "private-thought", "private-body", "synthetic-private-reason", "synthetic-response-key"))
    assert len(requests) == 1


def test_normal_reply_is_preserved_without_retry(transport):
    requests = transport({"choices": [{"message": {"content": "  original answer\n"}, "finish_reason": "stop"}]})
    assert invoke() == "  original answer\n"
    assert len(requests) == 1


@pytest.mark.parametrize("fields", [{"function_call": {"name": "synthetic", "arguments": "private"}},
                                    {"tool_calls": {"malformed": "private"}}])
def test_legacy_or_malformed_native_call_is_not_a_recoverable_empty(transport, fields):
    transport({"choices": [{"message": {"content": None, **fields}, "finish_reason": "stop"}]})
    with pytest.raises(RuntimeError, match="^llm_empty_response$") as error:
        invoke()
    assert error.value.native_tool_calls is True
