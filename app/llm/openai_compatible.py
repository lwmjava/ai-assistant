"""OpenAI 兼容大模型提供商。

OpenAI 官方接口与 Ollama 暴露的 ``/v1`` 接口均兼容同一套请求 / 响应格式，
因此同一实现即可覆盖两者，通过 ``LLM_BASE_URL`` / ``LLM_API_KEY`` /
``LLM_DEFAULT_MODEL`` 切换目标服务。
"""

import json
import logging
from collections.abc import AsyncIterator

import httpx

from app.core.config import settings
from app.llm.base import ChatMessage, LLMOptions, LLMProvider
from app.llm.budget import enforce_generation_budget
from app.llm.capabilities import (
    GenerationCapability,
    resolve_effective_capability,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(LLMProvider):
    """基于 OpenAI Chat Completions 接口的提供商（兼容 Ollama）。

    每次真正发出请求前核对上下文预算（RAG-028）。``capability`` 显式传入时
    按传入的那条判，否则按本跳自己的 base_url + model 现解析——意图、兜底各跳
    因此是按各自的窗口判，不共用对话主模型的窗口。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        capability: GenerationCapability | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.capability = capability

    def _effective_capability(self) -> GenerationCapability | None:
        """声明优先于解析；都没拿到就是「无批准配置」。"""
        if self.capability is not None:
            return self.capability
        return resolve_effective_capability(self.base_url, self.model)

    def _reserved_output(self, options: LLMOptions | None) -> int:
        """输出预留：显式给了 max_tokens 就用它，否则用配置的输出预留。"""
        opts = options or LLMOptions()
        if opts.max_tokens is not None:
            return opts.max_tokens
        return settings.LLM_OUTPUT_RESERVE_TOKENS

    def _guard(self, messages: list[ChatMessage], options: LLMOptions | None) -> None:
        """发请求前核对预算。输入就是本次真正要发的 messages。

        工具返回、自纠错轮次、历史都在 messages 里，因此天然被计入。
        违反时抛错，请求不会发出。
        """
        if not settings.LLM_CAPABILITY_GUARD_ENABLED:
            return
        capability = self._effective_capability()
        margin = settings.LLM_BUDGET_SAFETY_MARGIN + (
            capability.safety_margin if capability else 0
        )
        enforce_generation_budget(
            capability, messages, self._reserved_output(options), margin
        )

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
        self._guard(messages, options)
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
        self._guard(messages, options)
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
