"""Loopback-only acceptance instrumentation around the existing disposable UI fixture."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from tests.manual_sse_ui import main  # noqa: E402

LOG = ROOT / "data" / "sse-page-probe-20261017.jsonl"
original_run = uvicorn.run


def record(**fields):
    with LOG.open("a", encoding="utf-8") as output:
        output.write(json.dumps(fields, ensure_ascii=False) + "\n")


class RecordedTransport:
    def __init__(self, app):
        self.app = app
        self.interrupted = False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        headers = dict(scope.get("headers", []))
        replay = False
        tokens = 0
        record(kind="request", method=scope["method"], path=path,
               cursor=headers.get(b"last-event-id", b"").decode())

        async def observed_receive():
            nonlocal replay
            message = await receive()
            if b"UI_REPLAY_PROBE" in message.get("body", b""):
                replay = True
            return message

        async def observed_send(message):
            nonlocal tokens
            if message["type"] == "http.response.start":
                record(kind="response", path=path, status=message["status"])
            body = message.get("body", b"")
            for line in body.splitlines():
                if line.startswith(b"event: "):
                    event = line[7:].decode()
                    record(kind="event", path=path, event=event)
                    tokens += event == "token"
            await send(message)
            if replay and tokens >= 2 and not self.interrupted:
                self.interrupted = True
                record(kind="injected_transport_disconnect", path=path)
                raise ConnectionResetError("synthetic acceptance transport interruption")

        await self.app(scope, observed_receive, observed_send)


def run(app, **kwargs):
    return original_run(RecordedTransport(app), **kwargs)


if __name__ == "__main__":
    uvicorn.run = run
    main(port=18767)
