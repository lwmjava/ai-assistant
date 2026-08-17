"""LLM 提供商层测试（使用 Mock 提供商，不依赖网络）。"""

import asyncio

from app.llm.base import ChatMessage, ChatRole
from app.llm.mock import MockLLMProvider


def test_mock_chat_includes_user_input() -> None:
    provider = MockLLMProvider()
    out = asyncio.run(
        provider.chat([ChatMessage(role=ChatRole.USER, content="你好世界")])
    )
    assert "你好世界" in out


def test_mock_stream_yields_content() -> None:
    provider = MockLLMProvider()
    chunks = asyncio.run(_collect(provider))
    assert chunks
    assert "".join(chunks)


async def _collect(provider: MockLLMProvider) -> list[str]:
    return [
        chunk
        async for chunk in provider.stream_chat(
            [ChatMessage(role=ChatRole.USER, content="x")]
        )
    ]
