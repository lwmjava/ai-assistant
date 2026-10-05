"""Trace ID 中间件：为每个请求生成/复用 trace_id，贯穿日志并回写响应头。

- 请求带 `X-Request-ID` 头时复用该值（便于客户端关联自己的 trace）。
- 否则生成 uuid4 hex 作为 trace_id。
- 请求结束后清空 log_context，避免串扰下一请求。
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core import log_context

_X_REQUEST_ID = "X-Request-ID"


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为一次 HTTP 请求建立日志上下文，并在响应头回写 trace_id。"""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        trace_id = request.headers.get(_X_REQUEST_ID) or uuid.uuid4().hex[:32]
        log_context.set_trace_id(trace_id)
        try:
            response = await call_next(request)
        finally:
            log_context.clear()
        response.headers[_X_REQUEST_ID] = trace_id
        return response
