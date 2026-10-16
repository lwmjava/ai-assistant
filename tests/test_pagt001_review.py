"""Independent entry-level adversarial review; isolated disk/HTTP only."""

import asyncio
import importlib
import json
import os
import subprocess

import httpx
import pytest

from app.agents.tools.base import ToolCall, ToolRegistry
from app.agents.tools.builtin import default_tools
from app.core.config import settings

file_module = importlib.import_module("app.agents.tools.file_ops")
search_module = importlib.import_module("app.agents.tools.web_search")


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "disabled")
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", False)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", "{}")
    monkeypatch.setattr(settings, "FILE_OPS_MAX_BYTES", 65536)
    monkeypatch.setattr(settings, "SEARCH_DASHSCOPE_API_KEY", "review-synthetic-key")
    monkeypatch.setattr(settings, "SEARCH_DASHSCOPE_MODEL", "qwen-plus")
    monkeypatch.setattr(settings, "SEARCH_TIMEOUT_SECONDS", 15.0)
    monkeypatch.setattr(settings, "SEARCH_MAX_RESULTS", 5)
    monkeypatch.setattr(settings, "SEARCH_MAX_RESPONSE_BYTES", 1048576)


def configure_files(monkeypatch, root):
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", json.dumps({"public": str(root)}))


def run_tool(name, arguments):
    return asyncio.run(ToolRegistry(default_tools()).run(ToolCall(name, arguments)))


def test_review_real_open_handle_reads_only_public_bytes(monkeypatch, tmp_path):
    configure_files(monkeypatch, tmp_path)
    path = tmp_path / "public.txt"
    path.write_text("公开验收", encoding="utf-8")
    result = run_tool("file_ops", {"root": "public", "path": "public.txt"})
    assert '"status": "ok"' in result and "公开验收" in result
    assert str(tmp_path) not in result
    assert path.read_text(encoding="utf-8") == "公开验收"


def test_review_hardlinked_private_file_never_reaches_observation(monkeypatch, tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    private = tmp_path / "private.txt"
    private.write_text("ISOLATED_PRIVATE_DATA", encoding="utf-8")
    os.link(private, public / "notice.txt")
    configure_files(monkeypatch, public)
    result = run_tool("file_ops", {"root": "public", "path": "notice.txt"})
    assert '"status": "error"' in result and "ISOLATED_PRIVATE_DATA" not in result


def test_review_replacement_between_stat_and_open_is_refused(monkeypatch, tmp_path):
    configure_files(monkeypatch, tmp_path)
    target = tmp_path / "notice.txt"
    target.write_text("PUBLIC", encoding="utf-8")
    replacement = tmp_path / "replacement.txt"
    replacement.write_text("ISOLATED_REPLACEMENT", encoding="utf-8")
    original_open = os.open

    def raced_open(path, flags, *args, **kwargs):
        if path == target:
            os.replace(replacement, target)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(file_module.os, "open", raced_open)
    result = run_tool("file_ops", {"root": "public", "path": "notice.txt"})
    assert '"status": "error"' in result and "ISOLATED_REPLACEMENT" not in result


def test_review_windows_junction_escape_is_refused(monkeypatch, tmp_path):
    if os.name != "nt":
        return  # This actual Windows regression is inapplicable on POSIX.
    public = tmp_path / "public"
    private = tmp_path / "private"
    public.mkdir()
    private.mkdir()
    (private / "notice.txt").write_text("ISOLATED_PRIVATE_DATA", encoding="utf-8")
    junction = public / "shortcut"
    process = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(private)],
        capture_output=True, check=False,
    )
    assert process.returncode == 0
    try:
        configure_files(monkeypatch, public)
        result = run_tool("file_ops", {"root": "public", "path": "shortcut/notice.txt"})
        assert '"status": "error"' in result and "ISOLATED_PRIVATE_DATA" not in result
    finally:
        os.rmdir(junction)


@pytest.mark.parametrize("arguments", [
    {"root": "public", "path": "C:/synthetic-host/password.txt"},
    {"root": "public", "path": "public.txt", "api_key": "ISOLATED_INPUT_SECRET"},
    {"root": "public", "path": "public.txt", "action": "write"},
])
def test_review_registry_rejected_arguments_are_not_echoed(monkeypatch, tmp_path, arguments):
    configure_files(monkeypatch, tmp_path)
    (tmp_path / "public.txt").write_text("PUBLIC", encoding="utf-8")
    result = run_tool("file_ops", arguments)
    assert '"status": "error"' in result
    assert "ISOLATED_INPUT_SECRET" not in result and "synthetic-host" not in result
    assert (tmp_path / "public.txt").read_text(encoding="utf-8") == "PUBLIC"


@pytest.mark.parametrize("source_url", [
    "file:///C:/synthetic-host/private.txt", "https://localhost/private",
    "https://127.0.0.1/private", "https://example.org/review-synthetic-key",
])
def test_review_search_invalid_sources_are_not_success(monkeypatch, source_url):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "dashscope")
    original_client = httpx.AsyncClient

    def provider(request):
        assert request.url.host == "dashscope.aliyuncs.com"
        return httpx.Response(200, json={"output": {"search_info": {"search_results": [
            {"title": "result", "url": source_url}
        ]}}})

    monkeypatch.setattr(search_module.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(provider), **kwargs
    ))
    result = run_tool("web_search", {"query": "公开数据"})
    assert '"status": "empty"' in result
    assert source_url not in result and "review-synthetic-key" not in result


def test_review_search_cancel_propagates_without_fallback(monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "dashscope")
    original_client = httpx.AsyncClient
    calls = []

    async def provider(request):
        calls.append(request.url.host)
        raise asyncio.CancelledError

    monkeypatch.setattr(search_module.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(provider), **kwargs
    ))
    with pytest.raises(asyncio.CancelledError):
        run_tool("web_search", {"query": "公开数据"})
    assert calls == ["dashscope.aliyuncs.com"]


def test_review_deep_json_configuration_does_not_break_registration(monkeypatch):
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", "[" * 1500 + "0" + "]" * 1500)
    assert {tool.name for tool in default_tools()} == {
        "calculator", "code_sandbox", "get_current_datetime", "web_fetch"
    }


def test_review_deep_json_configuration_does_not_break_health(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", "[" * 1500 + "0" + "]" * 1500)
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["checks"]["tools"]["file_ops"]["status"] == "unavailable"


def test_review_deep_json_provider_response_is_stable_error(monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "dashscope")
    original_client = httpx.AsyncClient

    def provider(request):
        return httpx.Response(200, content=("[" * 1500 + "0" + "]" * 1500).encode())

    monkeypatch.setattr(search_module.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(provider), **kwargs
    ))
    result = run_tool("web_search", {"query": "公开数据"})
    assert '"code": "invalid_response"' in result


@pytest.mark.parametrize("provider", ["dashscope", "zhipu", "bocha", "baidu"])
def test_review_domestic_protocol_and_original_source_shape(monkeypatch, provider):
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", provider)
    monkeypatch.setattr(settings, f"SEARCH_{provider.upper()}_API_KEY", "review-synthetic-key")
    monkeypatch.setattr(settings, "SEARCH_ZHIPU_MODEL", "web-search-pro")
    monkeypatch.setattr(settings, "SEARCH_BAIDU_MODEL", "ernie-4.5-turbo-32k")
    original_client = httpx.AsyncClient
    calls = []

    def remote(request):
        calls.append(request)
        body = json.loads(request.content)
        assert request.headers["accept-encoding"] == "identity"
        if provider == "dashscope":
            assert request.url.host == "dashscope.aliyuncs.com"
            assert body["model"] == "qwen-plus"
            assert body["parameters"]["search_options"]["forced_search"] is True
            response = {"output": {"search_info": {"search_results": [
                {"title": "SOURCE_TITLE", "url": "https://example.org/source"}
            ]}}}
        elif provider == "zhipu":
            assert request.url.host == "open.bigmodel.cn"
            assert body["model"] == "web-search-pro" and body["stream"] is False
            response = {"choices": [{"message": {"tool_calls": [{"search_result": [
                {"title": "SOURCE_TITLE", "link": "https://example.org/source", "content": "SOURCE_EXCERPT"}
            ]}]}}]}
        elif provider == "bocha":
            assert request.url.host == "api.bochaai.com"
            assert body == {"query": "公开资料", "summary": True, "count": 1, "freshness": "noLimit"}
            response = {"code": 200, "data": {"webPages": {"value": [
                {"name": "SOURCE_TITLE", "url": "https://example.org/source", "summary": "SOURCE_EXCERPT"}
            ]}}}
        else:
            assert request.url.host == "qianfan.baidubce.com"
            assert request.headers["X-Appbuilder-Authorization"] == "Bearer review-synthetic-key"
            assert body["model"] == "ernie-4.5-turbo-32k"
            assert body["search_mode"] == "required" and body["search_source"] == "baidu_search_v2"
            assert body["enable_deep_search"] is False
            assert "enable_followup_query" not in body
            assert body["resource_type_filter"] == [{"type": "web", "top_k": 1}]
            response = {"references": [
                {"title": "SOURCE_TITLE", "url": "https://example.org/source", "content": "SOURCE_EXCERPT"}
            ]}
        return httpx.Response(200, json=response)

    monkeypatch.setattr(search_module.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(remote), **kwargs
    ))
    result = run_tool("web_search", {"query": "公开资料", "max_results": 1})
    assert '"status": "ok"' in result and "SOURCE_TITLE" in result
    assert "https://example.org/source" in result and "review-synthetic-key" not in result
    if provider != "dashscope":
        assert "SOURCE_EXCERPT" in result
    assert len(calls) == 1
