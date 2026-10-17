"""NFR-002 结构化日志：JSON 格式 + trace_id / user_id / tenant_id 贯穿。"""

import json
import logging

from app.core import log_context
from app.core.json_logging import JsonLogFormatter


def _make_record(message="hello", name="app.core.test", level=logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_log_formatter_emits_valid_json() -> None:
    log_context.clear()
    line = JsonLogFormatter().format(_make_record())
    data = json.loads(line)
    assert data["message"] == "hello"
    assert data["trace_id"] == ""
    assert "ts" in data
    assert "level" in data
    assert "logger" in data


def test_log_formatter_carries_trace_and_user() -> None:
    log_context.clear()
    log_context.set_trace_id("trace-123")
    log_context.set_user("user-1", "tenant-9")
    data = json.loads(JsonLogFormatter().format(_make_record(message="ctx")))
    assert data["trace_id"] == "trace-123"
    assert data["user_id"] == "user-1"
    assert data["tenant_id"] == "tenant-9"


def test_log_context_clear_resets_all() -> None:
    log_context.set_trace_id("x")
    log_context.set_user("u", "t")
    log_context.clear()
    assert log_context.get_trace_id() == ""
    assert log_context.get_user_id() == ""
    assert log_context.get_tenant_id() == ""


def test_setup_logging_is_idempotent() -> None:
    from app.core.json_logging import setup_logging

    setup_logging()
    setup_logging()  # 幂等：不抛错、不叠层


def test_trace_middleware_echoes_header() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.trace_middleware import TraceIdMiddleware

    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/ping")
    assert resp.headers.get("X-Request-ID")

    resp2 = client.get("/ping", headers={"X-Request-ID": "my-trace"})
    assert resp2.headers.get("X-Request-ID") == "my-trace"


def test_log_is_json_line_with_trace_end_to_end() -> None:
    """一次请求内，中间件注入的 trace_id 必须贯穿到日志 JSON 行。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.trace_middleware import TraceIdMiddleware

    captured = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(self.format(record))

    logger = logging.getLogger("app.core.trace_e2e")
    handler = _Capture()
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        app = FastAPI()
        app.add_middleware(TraceIdMiddleware)

        @app.get("/trace-e2e")
        def trace_e2e():
            logger.info("e2e message")
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/trace-e2e", headers={"X-Request-ID": "trace-e2e-1"})
        assert resp.headers.get("X-Request-ID") == "trace-e2e-1"
    finally:
        logger.removeHandler(handler)

    json_lines = [json.loads(line) for line in captured if "e2e message" in line]
    assert json_lines, "应捕获到一条日志"
    assert json_lines[0]["trace_id"] == "trace-e2e-1"
    assert json_lines[0]["message"] == "e2e message"
