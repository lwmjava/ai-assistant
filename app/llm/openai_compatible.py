"""OpenAI 兼容大模型提供商。

OpenAI 官方接口与 Ollama 暴露的 ``/v1`` 接口均兼容同一套请求 / 响应格式，
因此同一实现即可覆盖两者，通过 ``LLM_BASE_URL`` / ``LLM_API_KEY`` /
``LLM_DEFAULT_MODEL`` 切换目标服务。
"""

import json
import logging
from collections.abc import AsyncIterator

import httpx

from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(LLMProvider):
    """基于 OpenAI Chat Completions 接口的提供商（兼容 Ollama）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key or ''}",
            "Content-Type": "application/json",
        }

    def _payload(
        self, messages: list[ChatMessage], options: LLMOptions | None, stream: bool
    ) -> dict:
        opts = options or LLMOptions()
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": m.role.value, "content": m.content} for m in messages
            ],
            "stream": stream,
        }
        if opts.temperature is not None:
            payload["temperature"] = opts.temperature
        if opts.max_tokens is not None:
            payload["max_tokens"] = opts.max_tokens
        return payload

    async def chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> str:
        opts = options or LLMOptions()
        async with httpx.AsyncClient(timeout=opts.timeout or self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=self._payload(messages, options, False),
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        opts = options or LLMOptions()
        async with httpx.AsyncClient(timeout=opts.timeout or self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=self._payload(messages, options, True),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
