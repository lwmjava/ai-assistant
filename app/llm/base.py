"""LLM 提供商抽象层。

定义与大模型交互的最小接口，屏蔽 OpenAI / Ollama / Mock 等具体实现差异。
业务层（Agent 编排、检索等）只依赖本模块定义的抽象，便于替换与测试。
"""

import base64
import binascii
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatRole(str, Enum):
    """对话角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class TextContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    type: Literal["text"] = "text"
    text: str


class ImageURL(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    url: str = Field(repr=False)
    detail: Literal["low"] = "low"

    @field_validator("url")
    @classmethod
    def validate_data_url(cls, value: str) -> str:
        prefix, separator, encoded = value.partition(",")
        if not separator or prefix not in {"data:image/png;base64", "data:image/jpeg;base64"} or not encoded:
            raise ValueError("图片必须是受支持的 base64 data URL")
        try:
            base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("图片 base64 编码无效") from exc
        return value


class ImageContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    type: Literal["image_url"] = "image_url"
    image_url: ImageURL


ChatContent = str | list[TextContentPart | ImageContentPart]


def content_text(content: ChatContent) -> str:
    """文字历史/提示词只读文字段，绝不把图片编码插值成文本。"""
    if isinstance(content, str):
        return content
    return "".join(part.text for part in content if isinstance(part, TextContentPart))


def user_message_content(text: str, image_urls: Sequence[str] = ()) -> ChatContent:
    if not image_urls:
        return text
    return [TextContentPart(text=text), *[ImageContentPart(image_url=ImageURL(url=url)) for url in image_urls]]


class ChatMessage(BaseModel):
    """单条对话消息。"""

    model_config = ConfigDict(hide_input_in_errors=True)

    role: ChatRole
    content: ChatContent


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
    def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        """发送对话消息，增量返回模型生成的文本片段。"""
