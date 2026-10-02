"""核对 /api 业务路由是否出现在 OpenAPI 中，以及 README 端点表是否指向这些路径。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from fastapi.routing import APIRoute

from app.main import app

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_SKIP_METHODS = frozenset({"HEAD", "OPTIONS"})
_README = Path(__file__).resolve().parents[1] / "README.md"
_Operation = tuple[str, str]


def business_operations(routes: Iterable[object]) -> set[_Operation]:
    """收集纳入文档、路径以 /api 开头的路由方法。"""
    found: set[_Operation] = set()
    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.include_in_schema or not route.path.startswith("/api"):
            continue
        for method in route.methods:
            if method in _SKIP_METHODS:
                continue
            found.add((route.path, method))
    return found


def hidden_api_paths(routes: Iterable[object]) -> list[str]:
    """返回被排除在文档之外的 /api 路由路径。"""
    hidden: list[str] = []
    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path.startswith("/api") and not route.include_in_schema:
            hidden.append(route.path)
    return hidden


def openapi_operations(schema: dict) -> dict[_Operation, dict]:
    """按路径和方法取出 OpenAPI 操作对象。方法名为大写。"""
    found: dict[_Operation, dict] = {}
    for path, item in schema.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            upper = method.upper()
            if upper not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            found[(path, upper)] = operation
    return found


def missing_operations(registered: set[_Operation], documented: set[_Operation]) -> list[str]:
    """返回已注册但文档中没有的「方法 路径」。"""
    return [f"{method} {path}" for path, method in sorted(registered - documented)]


def operation_has_text(operation: dict) -> bool:
    """操作有非空摘要或说明时为真。"""
    summary = str(operation.get("summary") or "").strip()
    description = str(operation.get("description") or "").strip()
    return bool(summary or description)


def response_media_types(operation: dict) -> set[str]:
    """收集该操作全部响应声明的媒体类型。"""
    media: set[str] = set()
    responses = operation.get("responses") or {}
    if not isinstance(responses, dict):
        return media
    for response in responses.values():
        if isinstance(response, dict):
            content = response.get("content") or {}
            if isinstance(content, dict):
                media.update(str(key) for key in content)
    return media


def readme_endpoint_rows(markdown: str) -> list[_Operation]:
    """读取「API 端点」表中的路径和方法。一行里的多个方法各算一条。"""
    lines = markdown.splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if line.strip() == "## API 端点"), None)
    if start is None:
        return []
    rows: list[_Operation] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or "---" in cells[1] or cells[1] == "方法":
            continue
        methods = re.findall(r"`([A-Z]+)`", cells[1])
        path_match = re.search(r"`([^`]+)`", cells[2])
        if path_match is None:
            continue
        path = path_match.group(1).strip()
        for method in methods:
            if method in _HTTP_METHODS:
                rows.append((path, method))
    return rows


def test_openapi_covers_business_routes() -> None:
    """每个 /api 业务路由都在 OpenAPI 里，并且带有摘要或说明。"""
    schema = app.openapi()
    registered = business_operations(app.routes)
    documented = openapi_operations(schema)
    assert hidden_api_paths(app.routes) == []
    assert missing_operations(registered, set(documented)) == []
    missing_text = [
        f"{method} {path}"
        for (path, method), operation in sorted(documented.items())
        if path.startswith("/api") and not operation_has_text(operation)
    ]
    assert missing_text == []


def test_openapi_readme_paths_are_subset() -> None:
    """README 端点表中的方法加路径都能在 OpenAPI 里找到。"""
    documented = set(openapi_operations(app.openapi()))
    rows = readme_endpoint_rows(_README.read_text(encoding="utf-8"))
    assert rows
    absent = [f"{method} {path}" for path, method in rows if (path, method) not in documented]
    assert absent == []


def test_openapi_chat_stream_is_event_stream() -> None:
    """流式对话的文档声明响应媒体类型为 text/event-stream。"""
    operation = openapi_operations(app.openapi())[("/api/chat/stream", "POST")]
    assert "text/event-stream" in response_media_types(operation)


def test_openapi_missing_operation_is_reported() -> None:
    """比较函数能发现假路由表中多出来的一条，补上后不再报告。"""
    registered = {("/api/example", "GET")}
    assert missing_operations(registered, set()) == ["GET /api/example"]
    assert missing_operations(registered, {("/api/example", "GET")}) == []
