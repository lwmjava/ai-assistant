"""超窗压缩回归。

默认窗口 20、阈值 30、保留 5。25 条只滑动窗口，31 条压缩并保留最后 5 条原文。
保留条数按消息条数计算。
"""

import pytest

from app.llm.base import ChatMessage, ChatRole, LLMOptions
from app.memory.base import MemoryConfig
from app.memory.manager import MemoryManager

_FIXED_SUMMARY = "固定摘要：较早的对话已压缩。"


def _messages(count: int) -> list[ChatMessage]:
    return [
        ChatMessage(role=ChatRole.USER, content=f"body-{index:02d}")
        for index in range(1, count + 1)
    ]


def _default_config() -> MemoryConfig:
    config = MemoryConfig()
    assert config.window_size == 20
    assert config.compression_threshold == 30
    assert config.keep_recent == 5
    return config


class _FixedSummaryLLM:
    """测试用假模型：返回固定摘要，并记下用户提示。"""

    def __init__(self, summary: str) -> None:
        self._summary = summary
        self.user_prompts: list[str] = []

    async def chat(
        self,
        messages: list[ChatMessage],
        options: LLMOptions | None = None,
    ) -> str:
        del options
        for message in messages:
            if message.role == ChatRole.USER:
                self.user_prompts.append(message.content)
        return self._summary


@pytest.mark.asyncio
async def test_twenty_five_messages_slide_without_compression() -> None:
    """总条数 25，窗口 20，阈值 30，保留 5。未达阈值，只滑动窗口。留下正文序号 06–25。"""
    memory = await MemoryManager(config=_default_config()).manage(_messages(25))

    assert memory.is_compressed is False
    assert [item.content for item in memory.recent_messages] == [
        f"body-{index:02d}" for index in range(6, 26)
    ]


@pytest.mark.asyncio
async def test_thirty_one_messages_compress_and_keep_last_five() -> None:
    """总条数 31，窗口 20，阈值 30，保留 5。超过阈值后压缩前 26 条。留下正文序号 27–31。"""
    llm = _FixedSummaryLLM(_FIXED_SUMMARY)
    memory = await MemoryManager(llm=llm, config=_default_config()).manage(_messages(31))

    assert memory.is_compressed is True
    assert memory.snapshot.summary == _FIXED_SUMMARY
    assert memory.snapshot.compressed_count == 26
    kept = [item.content for item in memory.recent_messages]
    assert kept == [f"body-{index:02d}" for index in range(27, 32)]
    assert llm.user_prompts
    prompt = llm.user_prompts[0]
    for index in range(1, 27):
        body = f"body-{index:02d}"
        assert body in prompt
        assert body not in kept
    for index in range(27, 32):
        assert f"body-{index:02d}" not in prompt
