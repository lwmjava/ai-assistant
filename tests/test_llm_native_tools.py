"""Native function-call HTTP contract: send tools, accept one valid call, reject the rest."""

import asyncio
import json

import httpx
import pytest

from app.core.config import settings
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMToolDefinition
from app.llm.openai_compatible import LLMResponseError, OpenAICompatibleProvider

_TOOLS = [
    LLMToolDefinition(
        name="calculator",
        description="Evaluate an arithmetic expression",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    )
]


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

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
        )
        return requests

    return configure


def _invoke(options: LLMOptions | None = None) -> str:
    provider = OpenAICompatibleProvider(
        "https://isolated.invalid/v1", "synthetic-response-key", "local-test"
    )
    return asyncio.run(
        provider.chat(
            [ChatMessage(role=ChatRole.USER, content="2+3")],
            options or LLMOptions(temperature=0, timeout=1, tools=_TOOLS),
        )
    )


def _call(arguments: str = '{"expression":"2+3"}', *, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "calculator", "arguments": arguments},
    }


def test_text_request_does_not_send_tools_or_read_calls(transport) -> None:
    requests = transport(
        {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [_call()],
                },
                "finish_reason": "tool_calls",
            }]
        }
    )
    with pytest.raises(LLMResponseError, match="^llm_empty_response$") as error:
        _invoke(LLMOptions(temperature=0, timeout=1))
    payload = json.loads(requests[0].content)
    assert "tools" not in payload
    assert error.value.native_tool_calls is True


def test_native_request_sends_tools_and_normalizes_one_call(transport) -> None:
    requests = transport(
        {
            "choices": [{
                "message": {"content": None, "tool_calls": [_call()]},
                "finish_reason": "tool_calls",
            }]
        }
    )
    reply = _invoke()
    payload = json.loads(requests[0].content)
    assert payload["tool_choice"] == "auto"
    assert payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Evaluate an arithmetic expression",
                "parameters": _TOOLS[0].parameters,
            },
        }
    ]
    assert reply == '<tool_call>{"name":"calculator","arguments":{"expression":"2+3"}}</tool_call>'


@pytest.mark.parametrize(
    "body",
    [
        {
            "choices": [{
                "message": {
                    "content": "also text",
                    "tool_calls": [_call()],
                },
                "finish_reason": "tool_calls",
            }]
        },
        {
            "choices": [{
                "message": {"content": None, "tool_calls": [_call(), _call(call_id="call_2")]},
                "finish_reason": "tool_calls",
            }]
        },
        {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [_call(arguments='{"expression":"1","expression":"2"}')],
                },
                "finish_reason": "tool_calls",
            }]
        },
        {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [_call(arguments='{"expression": NaN}')],
                },
                "finish_reason": "tool_calls",
            }]
        },
        {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": "{}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }]
        },
        {
            "choices": [{
                "message": {
                    "content": '<tool_call>{"name":"calculator","arguments":{"expression":"2+3"}}</tool_call>',
                },
                "finish_reason": "stop",
            }]
        },
        {
            "choices": [{
                "message": {
                    "content": (
                        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="calculator">'
                        '<｜｜DSML｜｜ parameter name="expression" string="true">2+3'
                        "</｜｜DSML｜｜ parameter></｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>"
                    ),
                },
                "finish_reason": "stop",
            }]
        },
        {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [_call(arguments='{"expression":"2+3')],
                },
                "finish_reason": "length",
            }]
        },
    ],
)
def test_native_request_rejects_malformed_or_text_protocol(transport, body) -> None:
    transport(body)
    with pytest.raises(LLMResponseError, match="^llm_invalid_tool_call$"):
        _invoke()


def test_wrap_up_without_tools_rejects_native_calls(transport) -> None:
    requests = transport(
        {
            "choices": [{
                "message": {"content": None, "tool_calls": [_call()]},
                "finish_reason": "tool_calls",
            }]
        }
    )
    with pytest.raises(LLMResponseError, match="^llm_invalid_tool_call$"):
        _invoke(LLMOptions(temperature=0, timeout=1, tools=[]))
    payload = json.loads(requests[0].content)
    assert payload["tool_choice"] == "none"
    assert payload["tools"] == []


def test_stream_rejects_native_tool_requests() -> None:
    provider = OpenAICompatibleProvider(
        "https://isolated.invalid/v1", "synthetic-response-key", "local-test"
    )

    async def collect() -> None:
        async for _chunk in provider.stream_chat(
            [ChatMessage(role=ChatRole.USER, content="2+3")],
            LLMOptions(temperature=0, timeout=1, tools=_TOOLS),
        ):
            pass

    with pytest.raises(LLMResponseError, match="^llm_invalid_tool_call$"):
        asyncio.run(collect())


def test_native_tool_schema_is_counted_in_budget(transport, monkeypatch) -> None:
    counted: list[list] = []

    def fake(_capability, messages, _reserved, _margin):
        counted.append(list(messages))

    monkeypatch.setattr(settings, "LLM_CAPABILITY_GUARD_ENABLED", True)
    monkeypatch.setattr("app.llm.openai_compatible.enforce_generation_budget", fake)
    transport(
        {
            "choices": [{
                "message": {"content": None, "tool_calls": [_call()]},
                "finish_reason": "tool_calls",
            }]
        }
    )
    _invoke()
    assert counted
    extra = counted[0][-1]
    assert extra.role == ChatRole.SYSTEM
    assert "calculator" in extra.content


def test_native_request_bypasses_response_cache(transport, monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_RESPONSE_CACHE_ENABLED", True)
    requests = transport(
        {
            "model": "local-test",
            "choices": [{
                "message": {"content": None, "tool_calls": [_call()]},
                "finish_reason": "tool_calls",
            }],
        }
    )
    first = _invoke()
    second = _invoke()
    assert first == second
    assert len(requests) == 2
