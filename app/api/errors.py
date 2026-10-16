"""Stable public errors shared by HTTP responses and event payloads."""

import json
import math
from collections.abc import AsyncIterator, Mapping
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.log_context import get_trace_id

ErrorCode = Literal[
    "session_expired", "token_revoked", "user_disabled", "tenant_suspended",
    "rate_limited", "quota_exceeded", "model_unavailable", "file_too_large",
]
MESSAGES: dict[str, str] = {
    "session_expired": "登录已过期，请重新登录",
    "token_revoked": "您的登录凭证已被管理员重置，请重新登录",
    "user_disabled": "用户已被禁用，请联系管理员",
    "tenant_suspended": "租户已停用，请联系系统管理员",
    "rate_limited": "请求过于频繁，请稍后重试",
    "quota_exceeded": "本租户配额已用尽，请联系租户管理员",
    "model_unavailable": "模型服务暂不可用，请联系管理员检查模型配置",
    "file_too_large": "上传文件超过上限",
}


class PublicError(BaseModel):
    code: ErrorCode
    message: str
    retry_after: int | None
    request_id: str


class ErrorResponse(BaseModel):
    error: PublicError


class PublicHTTPError(HTTPException):
    """A named public failure with a compatible legacy detail."""

    def __init__(self, status_code: int, code: ErrorCode, *, detail: str | None = None,
                 headers: Mapping[str, str] | None = None, extra: dict | None = None):
        super().__init__(status_code, detail=detail or MESSAGES[code],
                         headers=dict(headers) if headers is not None else None)
        self.code = code
        self.extra = extra or {}


def retry_seconds(value: object) -> int | None:
    """Only finite, bounded countdowns can disable user input."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value <= 0 or value > 86400 or not math.isfinite(value):
        return None
    return math.ceil(value)


def error_payload(code: ErrorCode, *, retry_after: object = None,
                  legacy: dict | None = None, request_id: str | None = None) -> dict:
    fields = dict(legacy or {})
    message = MESSAGES[code]
    if code == "file_too_large" and isinstance(fields.get("limit_bytes"), int):
        message += f"（上限 {fields['limit_bytes']} 字节）"
    fields["error"] = PublicError(
        code=code, message=message, retry_after=retry_seconds(retry_after),
        request_id=request_id if request_id is not None else get_trace_id(),
    ).model_dump()
    return fields


def public_response(status_code: int, code: ErrorCode, *, legacy: dict | None = None,
                    retry_after: object = None, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code,
                        content=error_payload(code, retry_after=retry_after, legacy=legacy),
                        headers=headers)


async def public_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Keep unrelated validation semantics while normalizing explicitly named failures."""
    if isinstance(exc, PublicHTTPError):
        return public_response(exc.status_code, exc.code,
                               legacy={"detail": exc.detail, **exc.extra}, headers=exc.headers)
    assert isinstance(exc, StarletteHTTPException)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


def error_responses(*codes: ErrorCode) -> dict:
    statuses = {"session_expired": 401, "token_revoked": 401, "user_disabled": 403,
                "tenant_suspended": 403, "rate_limited": 429, "quota_exceeded": 429,
                "model_unavailable": 502, "file_too_large": 413}
    responses: dict = {}
    for code in codes:
        row = responses.setdefault(statuses[code], {"model": ErrorResponse, "description": "",
                                                   "content": {"application/json": {"examples": {}}}})
        row["description"] += f"{code}: {MESSAGES[code]}。"
        row["content"]["application/json"]["examples"][code] = {
            "value": error_payload(code, request_id="request-correlation-id")}
    return responses


async def public_events(events: AsyncIterator[dict[str, str]], request_id: str) -> AsyncIterator[dict[str, str]]:
    """Decorate already authorized errors without changing run IDs, ordering or ownership."""
    async for event in events:
        if event.get("event") == "error":
            fields = json.loads(event["data"])
            code = fields.get("code")
            if code in MESSAGES:
                payload = error_payload(code, legacy=fields,
                                        retry_after=fields.get("retry_after_seconds"), request_id=request_id)
                payload["message"] = payload["error"]["message"]
                event = {**event, "data": json.dumps(payload, ensure_ascii=False)}
        yield event


def complete_error_examples(schema: dict) -> dict:
    """Restore the explicit null countdown omitted by OpenAPI's generic encoder."""
    for path in schema.get("paths", {}).values():
        for operation in path.values():
            if not isinstance(operation, dict):
                continue
            for response in operation.get("responses", {}).values():
                content = response.get("content", {}).get("application/json", {})
                for example in content.get("examples", {}).values():
                    error = example.get("value", {}).get("error")
                    if isinstance(error, dict) and error.get("code") in MESSAGES:
                        error.setdefault("retry_after", None)
    return schema
