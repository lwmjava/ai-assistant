# Minimal SSE upstream for isolated reverse-proxy checks.
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write(f"{self.address_string()} - {format % args}\n")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/api/v1/health", "/health"}:
            self._json(200, {"status": "ok"})
            return
        if parsed.path.startswith("/api/v1/runs/") and parsed.path.endswith("/events"):
            self._sse(echo_last_event_id=True, idle_seconds=_idle(parsed.query))
            return
        if parsed.path in {"/", "/index.html"}:
            self._send(200, "text/html; charset=utf-8", b"<html><body>ok</body></html>")
            return
        self._json(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if parsed.path == "/api/v1/chat/stream":
            self._sse(echo_last_event_id=False, idle_seconds=_idle(parsed.query))
            return
        self._json(404, {"detail": "not found"})

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json", body)

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, echo_last_event_id: bool, idle_seconds: float) -> None:
        import time

        last_event_id = self.headers.get("Last-Event-ID") or self.headers.get("Last-Event-Id") or ""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(event: str, data: dict, event_id: str) -> None:
            payload = (
                f"id: {event_id}\n"
                f"event: {event}\n"
                f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            )
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()

        if echo_last_event_id:
            emit("replay", {"last_event_id": last_event_id}, "1")
            return
        emit("run.started", {"seq": 1}, "1")
        time.sleep(3)
        emit("token", {"seq": 2, "text": "hello"}, "2")
        if idle_seconds > 0:
            time.sleep(idle_seconds)
            emit("heartbeat", {"seq": 3}, "3")
        emit("done", {"seq": 4}, "4")


def _idle(query: str) -> float:
    values = parse_qs(query).get("idle", [])
    if not values:
        return float(os.environ.get("SSE_IDLE_SECONDS", "0"))
    return max(0.0, float(values[0]))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
