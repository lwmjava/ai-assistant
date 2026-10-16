"""Mock 大模型提供商（离线 / 测试用）。

不调用真实模型，按简单规则生成可预测的回复，便于离线开发、CI 与单元测试。
"""

import json
from collections.abc import AsyncIterator

from app.agents.prompts import SYSTEM_PLAN
from app.llm.base import ChatMessage, LLMOptions, LLMProvider, content_text


class MockLLMProvider(LLMProvider):
    """不连接真实大模型的占位实现。"""

    def __init__(self, model: str = "mock-model") -> None:
        self.model = model

    async def chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> str:
        if any(
            message.role.value == "system"
            and content_text(message.content).split("\n\n---\n# 激活的技能指令\n", 1)[0] == SYSTEM_PLAN
            for message in messages
        ):
            return json.dumps({"schema_version": 1, "steps": [{
                "id": "answer", "description": "综合当前上下文作答", "kind": "reason",
                "depends_on": [], "expected_output": "用户问题的回答草稿",
            }]}, ensure_ascii=False)
        last_user = _last_user_message(messages)
        return (
            f"[mock] 已收到你的消息：{last_user}\n"
            "（当前为 Mock 模型，未连接真实大模型；配置 LLM_API_KEY 后即可切换为实际提供商。）"
        )

    async def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        text = await self.chat(messages, options)
        for chunk in _chunked(text, size=16):
            yield chunk


def _last_user_message(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role.value == "user":
            return content_text(message.content)
    return ""


def _chunked(text: str, size: int = 16) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [text]
