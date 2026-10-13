"""Structured business failures retain legacy observations without guessing from prose."""

import json
import os

import httpx
import pytest

from app.agents.prompts import SYSTEM_PLAN
from app.agents.tools.base import Tool, ToolCall, ToolRegistry, format_tool_observation
from app.agents.tools.builtin import calculator, code_sandbox, default_tools, take_code_results
from app.agents.tools.executor import ToolExecutor
from app.agents.tools.file_ops import file_ops_tool, file_ops_write_tool
from app.agents.tools.web_search import web_search
from app.core.config import get_settings
from app.llm.base import ChatMessage, ChatRole
from app.llm.mock import MockLLMProvider


class QuietRegistry(ToolRegistry):
    async def _record_audit(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("expression,code", [
    ("", "missing_expression"), ("1/0", "invalid_expression"),
    ("2+", "invalid_expression"), ("1e309", "non_finite_result"),
    ("1e308 * 1e308", "non_finite_result"),
])
async def test_calculator_business_failure_is_not_success(expression, code):
    raw = await calculator({"expression": expression})
    registry = QuietRegistry(default_tools())
    result = await ToolExecutor(registry).execute(ToolCall("calculator", {"expression": expression}))
    assert result.observation == format_tool_observation("calculator", {"expression": expression}, raw)
    assert result.status == "error"
    assert getattr(result, "error_code", None) == code


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,code", [
    ("empty", "missing_url"), ("network", "network_error"), ("http", "http_error"),
])
async def test_web_failure_is_structured_and_keeps_text(monkeypatch, mode, code):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            if mode == "network":
                raise httpx.ConnectError("offline")
            return httpx.Response(503, text="unavailable")

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    args = {"url": "" if mode == "empty" else "https://example.invalid"}
    result = await ToolExecutor(QuietRegistry(default_tools())).execute(ToolCall("web_fetch", args))
    if mode == "empty":
        assert result.observation.endswith("未提供 url 参数。")
    elif mode == "network":
        assert result.observation.endswith("请求失败：offline")
    else:
        assert result.observation.endswith("HTTP 503，正文前 11 字符：\nunavailable")
    assert result.status == "error"
    assert getattr(result, "error_code", None) == code


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status,error_code", [
    ("", "error", "missing_code"), ("import os", "denied", "sandbox_denied"),
    ("1 / 0", "error", "sandbox_error"),
])
async def test_real_sandbox_failure_is_not_success(code, status, error_code):
    take_code_results()
    result = await ToolExecutor(QuietRegistry(default_tools())).execute(ToolCall("code_sandbox", {"code": code}))
    views = take_code_results()
    assert views and views[-1]["status"] != "ok"
    assert result.status == status
    assert getattr(result, "error_code", None) == error_code


@pytest.mark.asyncio
async def test_plain_error_like_prose_is_success_and_success_golden_unchanged():
    prose = "HTTP 500 appears in documentation. 计算失败 is an example."
    tool = Tool("documentation", "", {}, lambda args: prose)
    registry = QuietRegistry([tool])
    result = await ToolExecutor(registry).execute(ToolCall("documentation", {}))
    assert result.status == "ok"
    assert getattr(result, "error_code", None) is None
    assert result.observation == format_tool_observation("documentation", {}, prose)
    assert await calculator({"expression": "2+3"}) == "5"
    assert await code_sandbox({"code": "print('golden')"}) == "golden" + os.linesep
    take_code_results()


@pytest.mark.asyncio
async def test_real_sandbox_timeout_is_structured():
    take_code_results()
    result = await ToolExecutor(QuietRegistry(default_tools())).execute(
        ToolCall("code_sandbox", {"code": "while True:\n    pass", "timeout": 0.1})
    )
    assert result.status == "timeout"
    assert result.error_code == "sandbox_timeout"
    assert take_code_results()[-1]["status"] == "timeout"


@pytest.mark.asyncio
async def test_mock_only_plan_stage_returns_structured_plan():
    provider = MockLLMProvider()
    messages = [ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM_PLAN),
                ChatMessage(role=ChatRole.USER, content="summarize")]
    plan = json.loads(await provider.chat(messages))
    assert plan["schema_version"] == 1
    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["kind"] == "reason"
    assert plan["steps"][0]["depends_on"] == []
    messages[0] = ChatMessage(
        role=ChatRole.SYSTEM, content=SYSTEM_PLAN + "\n\n---\n# 激活的技能指令\nhelpful skill"
    )
    assert json.loads(await provider.chat(messages))["schema_version"] == 1
    messages[0] = ChatMessage(role=ChatRole.SYSTEM, content="ordinary system")
    assert (await provider.chat(messages)).startswith("[mock] 已收到你的消息：summarize")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["missing.txt", "../outside.txt"])
async def test_real_file_ops_failure_is_not_success(monkeypatch, tmp_path, path):
    settings = get_settings()
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", json.dumps({"public": str(tmp_path)}))
    tool = file_ops_tool()
    assert tool is not None
    args = {"root": "public", "path": path}
    raw = await tool.execute(args)
    assert json.loads(raw)["code"] == "access_denied"
    result = await ToolExecutor(QuietRegistry([tool])).execute(ToolCall("file_ops", args))
    assert result.observation == format_tool_observation("file_ops", args, raw)
    assert result.status == "error"
    assert result.error_code == "access_denied"
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_real_file_write_create_only_conflict_is_structured(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", json.dumps({"public": str(tmp_path)}))
    monkeypatch.setenv("FILE_OPS_WRITE_ENABLED", "true")
    tool = file_ops_write_tool()
    assert tool is not None
    args = {"root": "public", "path": "delivery.txt", "content": "first"}
    first = await tool.execute(args)
    assert json.loads(first)["status"] == "ok"
    second = await tool.execute({**args, "content": "replacement"})
    assert json.loads(second)["status"] == "conflict"
    assert (tmp_path / "delivery.txt").read_text() == "first"
    assert getattr(second, "status", None) == "error"
    assert getattr(second, "error_code", None) == "file_exists"


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_real_search_local_failures_are_structured_without_network(monkeypatch, configured):
    settings = get_settings()
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "dashscope" if configured else "disabled")
    monkeypatch.setattr(settings, "SEARCH_DASHSCOPE_API_KEY", "synthetic-test-only")
    tool = Tool("web_search", "", {}, web_search)
    args = {"query": ""}
    raw = await tool.execute(args)
    result = await ToolExecutor(QuietRegistry([tool])).execute(ToolCall("web_search", args))
    assert result.observation == format_tool_observation("web_search", args, raw)
    assert result.status == "error"
    assert result.error_code == ("invalid_arguments" if configured else "disabled")
