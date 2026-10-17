"""Domestic search and public shared-file read contracts (synthetic inputs only)."""

import asyncio
import importlib
import json
import os

import httpx
import pytest

from app.agents.tools.builtin import default_tools
from app.core.config import get_settings

search_module = importlib.import_module("app.agents.tools.web_search")
file_module = importlib.import_module("app.agents.tools.file_ops")


@pytest.fixture(autouse=True)
def isolate_configuration(monkeypatch):
    settings = get_settings()
    fields = {
        "SEARCH_PROVIDER": "disabled", "SEARCH_DASHSCOPE_MODEL": "qwen-plus",
        "SEARCH_ZHIPU_MODEL": "web-search-pro", "SEARCH_BAIDU_MODEL": "ernie-4.5-turbo-32k",
        "SEARCH_TIMEOUT_SECONDS": 15.0, "SEARCH_MAX_RESULTS": 5,
        "SEARCH_MAX_RESPONSE_BYTES": 1_048_576, "FILE_OPS_ENABLED": False,
        "FILE_OPS_ALLOWED_ROOTS": "{}", "FILE_OPS_MAX_BYTES": 65_536,
    }
    for provider in ("DASHSCOPE", "ZHIPU", "BOCHA", "BAIDU"):
        fields[f"SEARCH_{provider}_API_KEY"] = ""
    for name, value in fields.items():
        monkeypatch.setattr(settings, name, value)


def _search(monkeypatch, handler):
    settings = get_settings()
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "dashscope")
    monkeypatch.setattr(settings, "SEARCH_DASHSCOPE_API_KEY", "synthetic-test-only")
    original = httpx.AsyncClient
    monkeypatch.setattr(
        search_module.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )


def _file(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "FILE_OPS_ENABLED", True)
    monkeypatch.setattr(get_settings(), "FILE_OPS_ALLOWED_ROOTS", json.dumps({"public": str(tmp_path)}))
    (tmp_path / "notice.txt").write_text("公开共享资料\n", encoding="utf-8")
    return {"root": "public", "path": "notice.txt"}


def _invoke(func, arguments):
    return json.loads(asyncio.run(func(arguments)))


def test_configured_domestic_search_is_registered(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "dashscope", raising=False)
    monkeypatch.setattr(settings, "SEARCH_DASHSCOPE_API_KEY", "synthetic-test-only", raising=False)
    assert "web_search" in {tool.name for tool in default_tools()}


def test_enabled_public_file_reader_is_registered(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", json.dumps({"public": str(tmp_path)}), raising=False)
    assert "file_ops" in {tool.name for tool in default_tools()}


@pytest.mark.parametrize("field,value", [
    ("SEARCH_PROVIDER", "openai"), ("SEARCH_DASHSCOPE_MODEL", "gpt-4o"),
    ("SEARCH_TIMEOUT_SECONDS", float("nan")), ("SEARCH_TIMEOUT_SECONDS", float("inf")),
    ("SEARCH_TIMEOUT_SECONDS", True), ("SEARCH_TIMEOUT_SECONDS", 10**400),
    ("SEARCH_MAX_RESULTS", True), ("SEARCH_MAX_RESULTS", 0), ("SEARCH_MAX_RESULTS", 11),
    ("SEARCH_MAX_RESPONSE_BYTES", 0), ("SEARCH_MAX_RESPONSE_BYTES", 1_048_577),
])
def test_search_invalid_configuration_never_registers(monkeypatch, field, value):
    monkeypatch.setattr(get_settings(), "SEARCH_PROVIDER", "dashscope")
    monkeypatch.setattr(get_settings(), "SEARCH_DASHSCOPE_API_KEY", "synthetic-test-only")
    monkeypatch.setattr(get_settings(), field, value)
    assert search_module.search_tool() is None
    assert _invoke(search_module.web_search, {"query": "公开资料"})["status"] == "unavailable"


@pytest.mark.parametrize("arguments", [
    {}, {"query": ""}, {"query": "x" * 2001}, {"query": []},
    {"query": "x", "max_results": True}, {"query": "x", "max_results": float("nan")},
    {"query": "x", "max_results": 0}, {"query": "x", "max_results": 6},
    {"query": "x", "endpoint": "https://example.org"}, {"query": "x", "api_key": "pretend"},
])
def test_search_invalid_arguments_send_no_requests(monkeypatch, arguments):
    def no_request(request):
        raise AssertionError("invalid arguments reached network")
    _search(monkeypatch, no_request)
    assert _invoke(search_module.web_search, arguments)["code"] == "invalid_arguments"


def test_search_forced_domestic_request_and_bound_sources(monkeypatch):
    def provider(request):
        body = json.loads(request.content)
        assert body["model"] == "qwen-plus"
        assert body["parameters"]["search_options"] == {"forced_search": True, "enable_source": True}
        assert request.headers["accept-encoding"] == "identity"
        sources = [
            {"title": "unsafe", "url": "http://127.0.0.1/private"},
            {"title": "redacted synthetic-test-only", "url": "https://example.org/1", "snippet": "synthetic-test-only"},
            {"title": "duplicate", "url": "https://example.org/1"},
            {"title": "second", "url": "https://example.org/2"},
        ]
        return httpx.Response(200, json={"output": {"search_info": {"search_results": sources}}})
    _search(monkeypatch, provider)
    result = _invoke(search_module.web_search, {"query": "公开资料", "max_results": 1})
    assert result["status"] == "ok" and result["untrusted"] is True
    assert len(result["results"]) == 1
    assert "synthetic-test-only" not in json.dumps(result)


@pytest.mark.parametrize("response,code", [
    ({"model": "gpt-4o"}, "model_mismatch"),
    ({"error": {"message": "synthetic-test-only"}}, "upstream_unavailable"),
    ({"output": {"search_info": {"search_results": []}}}, "no_sources"),
    ({"output": None}, "invalid_response"),
    ([], "invalid_response"),
])
def test_search_untrusted_response_errors_are_safe(monkeypatch, response, code):
    _search(monkeypatch, lambda request: httpx.Response(200, json=response))
    result = _invoke(search_module.web_search, {"query": "公开资料"})
    assert result["code"] == code and "synthetic-test-only" not in json.dumps(result)


def test_search_cancellation_propagates(monkeypatch):
    def cancel(request):
        raise asyncio.CancelledError()
    _search(monkeypatch, cancel)
    with pytest.raises(asyncio.CancelledError):
        _invoke(search_module.web_search, {"query": "公开资料"})


def test_existing_search_object_obeys_disable(monkeypatch):
    _search(monkeypatch, lambda request: (_ for _ in ()).throw(AssertionError("network after disable")))
    tool = search_module.search_tool()
    assert tool is not None
    monkeypatch.setattr(get_settings(), "SEARCH_PROVIDER", "disabled")
    assert _invoke(tool.execute, {"query": "公开资料"})["status"] == "unavailable"


@pytest.mark.parametrize("path", [
    "../notice.txt", "/notice.txt", "C:/notice.txt", "nested\\notice.txt", "./notice.txt",
    "nested//notice.txt", ".env", ".git/config.txt", "tenant/notice.txt", "tenants/notice.txt",
    "knowledge/notice.txt", "password.txt", "key.pem", "app.db", "con.txt", "notice.txt:stream",
])
def test_file_denies_private_absolute_traversal_and_device_paths(monkeypatch, tmp_path, path):
    arguments = _file(monkeypatch, tmp_path)
    before = (tmp_path / "notice.txt").read_bytes()
    arguments["path"] = path
    assert _invoke(file_module.file_ops, arguments)["status"] != "ok"
    assert (tmp_path / "notice.txt").read_bytes() == before


@pytest.mark.parametrize("maximum", [True, 0, -1, 65_537, float("nan"), float("inf")])
def test_file_read_numeric_limits(monkeypatch, tmp_path, maximum):
    arguments = _file(monkeypatch, tmp_path)
    arguments["max_bytes"] = maximum
    assert _invoke(file_module.file_ops, arguments)["code"] == "invalid_arguments"


def test_file_bounded_utf8_and_repeat_is_read_only(monkeypatch, tmp_path):
    arguments = _file(monkeypatch, tmp_path)
    before = (tmp_path / "notice.txt").read_bytes()
    arguments["max_bytes"] = 4
    first = _invoke(file_module.file_ops, arguments)
    second = _invoke(file_module.file_ops, arguments)
    assert first == second
    assert first["content"] == "公" and first["truncated"] is True
    assert len(first["content"].encode("utf-8")) <= 4
    assert (tmp_path / "notice.txt").read_bytes() == before


@pytest.mark.parametrize("payload", [
    b"\x00\x01", b"\xff\xfe", b"api_key=synthetic-secret", b"-----BEGIN PRIVATE KEY-----",
])
def test_file_rejects_binary_and_secret_contents(monkeypatch, tmp_path, payload):
    arguments = _file(monkeypatch, tmp_path)
    (tmp_path / "notice.txt").write_bytes(payload)
    result = _invoke(file_module.file_ops, arguments)
    assert result["status"] == "error"
    assert "synthetic-secret" not in json.dumps(result)


def test_file_rejects_hardlink_and_open_object_swap(monkeypatch, tmp_path):
    arguments = _file(monkeypatch, tmp_path)
    target = tmp_path / "notice.txt"
    outside = tmp_path.parent / "outside-public.txt"
    outside.write_text("OUTSIDE", encoding="utf-8")
    os.link(outside, tmp_path / "link.txt")
    arguments["path"] = "link.txt"
    assert _invoke(file_module.file_ops, arguments)["status"] == "error"
    arguments["path"] = "notice.txt"
    original = os.open
    def swapped_open(path, flags):
        return original(outside, flags)
    monkeypatch.setattr(file_module.os, "open", swapped_open)
    result = _invoke(file_module.file_ops, arguments)
    assert result["status"] == "error" and "OUTSIDE" not in json.dumps(result)
    assert target.read_text(encoding="utf-8").startswith("公开")


def test_existing_file_object_obeys_disable(monkeypatch, tmp_path):
    arguments = _file(monkeypatch, tmp_path)
    tool = file_module.file_ops_tool()
    assert tool is not None
    monkeypatch.setattr(get_settings(), "FILE_OPS_ENABLED", False)
    assert _invoke(tool.execute, arguments)["status"] == "unavailable"
