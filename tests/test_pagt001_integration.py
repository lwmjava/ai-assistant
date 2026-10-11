"""PAGT-001 E2: real registration/Agent loop with isolated HTTP and disk.

The scripted model is a deterministic local test driver; no real model, key,
paid endpoint or production filesystem is used.
"""

import asyncio
import importlib
import json

import httpx
import pytest

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.tools.base import ToolCall, ToolRegistry
from app.agents.tools.builtin import default_tools
from app.core.config import settings


@pytest.fixture(autouse=True)
def isolated_tool_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "disabled")
    for provider in ("DASHSCOPE", "ZHIPU", "BOCHA", "BAIDU"):
        monkeypatch.setattr(settings, f"SEARCH_{provider}_API_KEY", "")
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", False)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", "{}")


def _driver(name, arguments):
    class LocalDriver:
        model = "local-scripted-evaluation-v1"
        used = False

        async def chat(self, messages, options=None):
            if "## 可用外部工具" in messages[-1].content and not self.used:
                self.used = True
                call = json.dumps({"name": name, "arguments": arguments})
                return f"<tool_call>{call}</tool_call>"
            if any("https://example.org/public" in item.content for item in messages):
                return "公开资料来源：https://example.org/public"
            return "已使用工具观测完成回答。"

        async def stream_chat(self, messages, options=None):
            yield "已使用工具观测完成回答。"

    return LocalDriver()


def _run_agent(name, arguments):
    pipeline = AgentPipeline(_driver(name, arguments), tools=ToolRegistry(default_tools()))
    state = asyncio.run(pipeline.run(AgentState(user_input="查阅公开资料并说明来源")))
    assert state.error is None
    assert state.answer
    assert len(state.tool_results) == 1
    return state.tool_results[0]


_SOURCE = {"title": "公开资料", "url": "https://example.org/public", "snippet": "来源摘要"}
_RESPONSES = {
    "dashscope": {"output": {"search_info": {"search_results": [_SOURCE]}}},
    "zhipu": {"choices": [{"message": {"tool_calls": [{"search_result": [_SOURCE]}]}}]},
    "bocha": {"code": 200, "data": {"webPages": {"value": [_SOURCE]}}},
    "baidu": {"references": [_SOURCE]},
}
_HOSTS = {
    "dashscope": "dashscope.aliyuncs.com",
    "zhipu": "open.bigmodel.cn",
    "bocha": "api.bochaai.com",
    "baidu": "qianfan.baidubce.com",
}


def _configure_http(monkeypatch, handler):
    module = importlib.import_module("app.agents.tools.web_search")
    original = httpx.AsyncClient

    def client(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(**kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", client)


@pytest.mark.parametrize("provider", tuple(_RESPONSES))
def test_tool_search_four_domestic_providers_through_agent(monkeypatch, provider):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", provider)
    monkeypatch.setattr(settings, f"SEARCH_{provider.upper()}_API_KEY", "synthetic-search-key")
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == _HOSTS[provider]
        assert request.method == "POST"
        assert "查找公开资料" in request.content.decode()
        return httpx.Response(200, json=_RESPONSES[provider])

    _configure_http(monkeypatch, handler)
    names = {tool.name for tool in default_tools()}
    assert "web_search" in names
    observation = _run_agent("web_search", {"query": "查找公开资料"})
    assert "https://example.org/public" in observation
    assert "公开资料" in observation
    assert provider in observation
    assert "synthetic-search-key" not in observation
    assert len(requests) == 1


def test_tool_health_missing_keys_and_disabled_files(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "dashscope")
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    checks = response.json()["checks"]["tools"]
    assert checks["web_search"]["status"] == "unavailable"
    assert checks["file_ops"]["status"] == "unavailable"
    assert {tool.name for tool in default_tools()} == {
        "calculator", "code_sandbox", "get_current_datetime", "web_fetch"
    }


@pytest.mark.parametrize("provider", tuple(_RESPONSES))
def test_tool_search_chat_api_persists_source_reply(monkeypatch, provider):
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.core.security import Role
    from app.llm.factory import set_llm_provider_override
    from app.main import app
    from app.models.user import User

    monkeypatch.setattr(settings, "SEARCH_PROVIDER", provider)
    monkeypatch.setattr(settings, f"SEARCH_{provider.upper()}_API_KEY", "synthetic-search-key")
    monkeypatch.setattr(settings, "RAG_ENABLED", False)
    monkeypatch.setattr(settings, "MCP_ENABLED", False)
    monkeypatch.setattr(settings, "MEMORY_ENABLED", False)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == _HOSTS[provider]
        return httpx.Response(200, json=_RESPONSES[provider])

    _configure_http(monkeypatch, handler)
    user = User(id="pagt-e2-user", tenant_id="pagt-e2-tenant", username="pagt-e2",
                hashed_password="", role=Role.MEMBER.value, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    set_llm_provider_override(_driver("web_search", {"query": "查找公开资料"}))
    try:
        with TestClient(app) as client:
            response = client.post("/api/chat", json={"message": "查找公开资料并注明来源"})
            assert response.status_code == 200
            body = response.json()
            assert "https://example.org/public" in body["reply"]
            assert len(requests) == 1
            detail = client.get(f"/api/chat/conversations/{body['conversation_id']}")
            assert detail.status_code == 200
            assistant = [item for item in detail.json()["messages"] if item["role"] == "assistant"]
            assert "https://example.org/public" in assistant[-1]["content"]
            assert client.delete(f"/api/chat/conversations/{body['conversation_id']}").status_code == 200
    finally:
        app.dependency_overrides.clear()
        set_llm_provider_override(None)


def test_tool_file_read_agent_and_disable_rollback(monkeypatch, tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    path = public / "notice.txt"
    path.write_text("共享公开资料", encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", json.dumps({"public": str(public)}))
    observation = _run_agent("file_ops", {"root": "public", "path": "notice.txt"})
    assert "共享公开资料" in observation
    assert str(public) not in observation
    assert path.read_bytes() == before
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", False)
    assert len(default_tools()) == 4
    assert asyncio.run(ToolRegistry(default_tools()).run(
        ToolCall("calculator", {"expression": "6*7"})
    )).endswith("结果=42")


@pytest.mark.parametrize("arguments", [
    {"root": "public", "path": "../private.txt"},
    {"root": "private", "path": "private.txt"},
    {"root": "public", "path": ".env"},
    {"root": "public", "path": "notice.txt", "action": "write"},
    {"root": "public", "path": "notice.txt", "tenant_id": "other-tenant"},
    {"root": "public", "path": "notice.txt", "max_bytes": float("nan")},
])
def test_tool_file_rejects_unauthorized_inputs_without_side_effect(monkeypatch, tmp_path, arguments):
    public = tmp_path / "public"
    public.mkdir()
    (public / "notice.txt").write_text("PUBLIC", encoding="utf-8")
    (public / ".env").write_text("SYNTHETIC_SECRET", encoding="utf-8")
    (tmp_path / "private.txt").write_text("PRIVATE_TENANT", encoding="utf-8")
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", json.dumps({"public": str(public)}))
    tool = ToolRegistry(default_tools()).get("file_ops")
    assert tool is not None
    payload = json.loads(asyncio.run(tool.execute(arguments)))
    assert payload["status"] != "ok"
    assert "SYNTHETIC_SECRET" not in json.dumps(payload)
    assert "PRIVATE_TENANT" not in json.dumps(payload)
    assert (public / "notice.txt").read_text(encoding="utf-8") == "PUBLIC"


@pytest.mark.parametrize("failure", ["timeout", "429", "500", "invalid-json", "oversize"])
def test_tool_search_failure_preserves_old_tools(monkeypatch, failure):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "dashscope")
    monkeypatch.setattr(settings, "SEARCH_DASHSCOPE_API_KEY", "synthetic-search-key")

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-search-key", request=request)
        if failure in {"429", "500"}:
            return httpx.Response(int(failure), text="synthetic-search-key")
        if failure == "oversize":
            return httpx.Response(200, content=b"x" * (settings.SEARCH_MAX_RESPONSE_BYTES + 1))
        return httpx.Response(200, text="synthetic-search-key-invalid-json")

    _configure_http(monkeypatch, handler)
    observation = _run_agent("web_search", {"query": "查找公开资料"})
    assert "synthetic-search-key" not in observation
    assert '"status": "ok"' not in observation
    assert "42" in asyncio.run(ToolRegistry(default_tools()).run(
        ToolCall("calculator", {"expression": "6*7"})
    ))


def test_tool_unknown_foreign_configuration_is_not_registered(monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "bing")
    assert "web_search" not in {tool.name for tool in default_tools()}
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "dashscope")
    monkeypatch.setattr(settings, "SEARCH_DASHSCOPE_API_KEY", "synthetic-search-key")
    monkeypatch.setattr(settings, "SEARCH_DASHSCOPE_MODEL", "gpt-4o")
    assert "web_search" not in {tool.name for tool in default_tools()}


def test_tool_search_cancellation_propagates_without_retry(monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "dashscope")
    monkeypatch.setattr(settings, "SEARCH_DASHSCOPE_API_KEY", "synthetic-search-key")
    requests = []

    async def handler(request):
        requests.append(request)
        raise asyncio.CancelledError()

    _configure_http(monkeypatch, handler)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ToolRegistry(default_tools()).run(ToolCall("web_search", {"query": "公开资料"})))
    assert len(requests) == 1


@pytest.mark.parametrize("url", [
    "file:///C:/private", "http://127.0.0.1/", "https://a:b@example.org/", "javascript:alert(1)"
])
def test_tool_search_discards_unsafe_source_urls(monkeypatch, url):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "dashscope")
    monkeypatch.setattr(settings, "SEARCH_DASHSCOPE_API_KEY", "synthetic-search-key")

    def handler(request):
        return httpx.Response(200, json={"output": {"search_info": {"search_results": [
            {"title": "unsafe", "url": url, "snippet": "untrusted"}
        ]}}})

    _configure_http(monkeypatch, handler)
    tool = ToolRegistry(default_tools()).get("web_search")
    assert tool is not None
    payload = json.loads(asyncio.run(tool.execute({"query": "公开资料"})))
    assert not payload.get("results")


def test_tool_registry_does_not_echo_rejected_sensitive_arguments(monkeypatch, tmp_path):
    """A safe helper error must stay safe in the actual Agent observation."""
    public = tmp_path / "public"
    public.mkdir()
    (public / "notice.txt").write_text("PUBLIC", encoding="utf-8")
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", json.dumps({"public": str(public)}))
    registry = ToolRegistry(default_tools())
    observation = asyncio.run(registry.run(ToolCall("file_ops", {
        "root": "public", "path": str(public / "notice.txt"), "api_key": "synthetic-sensitive-argument"
    })))
    assert "synthetic-sensitive-argument" not in observation
    assert str(public) not in observation


@pytest.mark.parametrize("name", ["web_search", "file_ops"])
def test_tool_registry_unexpected_error_is_safe(monkeypatch, tmp_path, caplog, name):
    public = tmp_path / "public"
    public.mkdir()
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "dashscope")
    monkeypatch.setattr(settings, "SEARCH_DASHSCOPE_API_KEY", "synthetic-search-key")
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", json.dumps({"public": str(public)}))
    registry = ToolRegistry(default_tools())
    tool = registry.get(name)
    assert tool is not None

    def fault(arguments):
        raise RuntimeError(f"synthetic-sensitive-argument {public}")

    tool.func = fault
    observation = asyncio.run(registry.run(ToolCall(name, {"api_key": "synthetic-sensitive-argument"})))
    assert "synthetic-sensitive-argument" not in observation + caplog.text
    assert str(public) not in observation + caplog.text
