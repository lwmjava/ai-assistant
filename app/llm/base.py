"""LLM 提供商抽象层。

定义与大模型交互的最小接口，屏蔽 OpenAI / Ollama / Mock 等具体实现差异。
业务层（Agent 编排、检索等）只依赖本模块定义的抽象，便于替换与测试。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import Enum

from pydantic import BaseModel


class ChatRole(str, Enum):
    """对话角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel):
    """单条对话消息。"""

    role: ChatRole
    content: str


class LLMOptions(BaseModel):
    """一次补全请求的可调参数（未设置则由提供商使用默认值）。"""

    temperature: float | None = None
    max_tokens: int | None = None
    timeout: float | None = None


class LLMProvider(ABC):
    """大模型提供商接口。

    实现方需提供 ``chat``（一次性返回完整文本）与 ``stream_chat``（增量返回）。
    """

    model: str = "unknown"

    @abstractmethod
    async def chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> str:
        """发送对话消息，返回模型生成的完整文本。"""

    @abstractmethod
    async def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        """发送对话消息，增量返回模型生成的文本片段。"""
