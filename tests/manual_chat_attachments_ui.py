"""Disposable, offline attachment UI fixture; never uses a real model or user file."""

import base64
import json
import struct
import tempfile
import zlib
from pathlib import Path

from tests.manual_sse_ui import main as run_fixture


def main() -> None:
    directory = Path(tempfile.mkdtemp(prefix="chat-ui-assets-", dir="data/pytest-tmp"))
    (directory / "review.md").write_text("UI_ATTACHMENT_DOCUMENT_MARKER\nA synthetic document.", encoding="utf-8")
    (directory / "blocked.tsx").write_text("synthetic rejected source", encoding="utf-8")
    (directory / "oversized.py").write_bytes(b"x" * (1048576 + 1))

    def chunk(kind, payload):
        checksum = struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
        return struct.pack(">I", len(payload)) + kind + payload + checksum

    image = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
             + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))
    (directory / "pixel.png").write_bytes(image)
    print("DISPOSABLE_UPLOAD_ASSETS=" + str(directory.resolve()), flush=True)
    print("DISPOSABLE_PNG_BASE64=" + base64.b64encode(image).decode(), flush=True)

    def provider_factory():
        import asyncio

        from app.llm.mock import MockLLMProvider
        from app.llm.openai_compatible import OpenAICompatibleProvider

        class RecordingFixture(OpenAICompatibleProvider):
            def __init__(self):
                # Official identity permits the verified image counter only; methods below never use HTTP.
                super().__init__("https://api.openai.com/v1", "offline-fixture-placeholder", "gpt-4o-mini")
                self.mock = MockLLMProvider()

            async def chat(self, messages, options=None):
                self._guard(messages, options)
                return await self.mock.chat(messages, options)

            async def stream_chat(self, messages, options=None):
                self._guard(messages, options)
                payload = json.dumps([message.model_dump(mode="json") for message in messages])
                document = "UI_ATTACHMENT_DOCUMENT_MARKER" in payload
                picture = "data:image/png;base64," in payload
                reply = (f"隔离载荷验证：文档正文{'已收到' if document else '未收到'}，"
                         f"图片内容{'已收到' if picture else '未收到'}。")
                for character in reply:
                    await asyncio.sleep(0.025)
                    yield character

        return RecordingFixture()

    # run_fixture disables dotenv and external networking before invoking provider_factory.
    run_fixture(provider_factory, port=18766)


if __name__ == "__main__":
    main()
