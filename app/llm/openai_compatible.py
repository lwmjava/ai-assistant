"""OpenAI 兼容大模型提供商。

OpenAI 官方接口与 Ollama 暴露的 ``/v1`` 接口均兼容同一套请求 / 响应格式，
因此同一实现即可覆盖两者，通过 ``LLM_BASE_URL`` / ``LLM_API_KEY`` /
``LLM_DEFAULT_MODEL`` 切换目标服务。
"""

import copy
import json
import logging
import math
from collections.abc import AsyncIterator

import httpx

from app.core.config import settings
from app.core.response_cache import BoundedCache, cache_limits, current_cache_snapshot, digest
from app.llm.base import ChatMessage, LLMOptions, LLMProvider
from app.llm.budget import enforce_generation_budget
from app.llm.capabilities import (
    GenerationCapability,
    resolve_effective_capability,
)

logger = logging.getLogger(__name__)
_responses = BoundedCache()


class LLMResponseError(RuntimeError):
    """Fixed code and allowlisted metadata, without response or reasoning bodies."""

    def __init__(self, error_code: str, *, finish_reason: object = None,
                 native_tool_calls: bool = False, reply: str | None = None) -> None:
        self.error_code = error_code
        self.finish_reason = (
            finish_reason if isinstance(finish_reason, str)
            and finish_reason in {"stop", "length", "tool_calls", "content_filter"}
            else "missing" if finish_reason is None else "other"
        )
        self.native_tool_calls = native_tool_calls
        self.reply = reply if isinstance(reply, str) and not reply.strip() else None
        super().__init__(error_code)


def _response_content(data: object) -> str:
    if not isinstance(data, dict):
        raise LLMResponseError("llm_invalid_response")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LLMResponseError("llm_invalid_response")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict) or "content" not in message:
        raise LLMResponseError("llm_invalid_response")
    content = message["content"]
    if content is None or (isinstance(content, str) and not content.strip()):
        raise LLMResponseError(
            "llm_empty_response", finish_reason=choice.get("finish_reason"),
            native_tool_calls=bool(message.get("tool_calls") or message.get("function_call")),
            reply=content,
        )
    if not isinstance(content, str):
        raise LLMResponseError("llm_invalid_response")
    return content


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
        from app.llm.vision import has_image_inputs, visual_capability

        images = has_image_inputs(messages)
        if not images and not settings.LLM_CAPABILITY_GUARD_ENABLED:
            return
        capability = visual_capability(self.base_url, self.model) if images else self._effective_capability()
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
                m.model_dump(mode="json") for m in messages
            ],
            "stream": stream,
        }
        if opts.temperature is not None:
            payload["temperature"] = opts.temperature
        if opts.max_tokens is not None:
            payload["max_tokens"] = opts.max_tokens
        return payload

    def _cache_request(
        self, payload: dict, opts: LLMOptions, headers: dict, url: str, timeout: float
    ) -> tuple[str, str] | None:
        if not settings.LLM_RESPONSE_CACHE_ENABLED or cache_limits() is None:
            return None
        if (opts.temperature is None or not math.isfinite(opts.temperature)
                or opts.temperature != 0 or (opts.max_tokens is not None and opts.max_tokens <= 0)):
            return None
        if not math.isfinite(timeout) or timeout <= 0:
            return None
        if opts.timeout is not None and (not math.isfinite(opts.timeout) or opts.timeout <= 0):
            return None
        stamp = current_cache_snapshot()
        if stamp is None:
            return None
        key = digest((stamp, url, digest(headers), payload, opts.model_dump(),
                      timeout, self._effective_capability()))
        return key, stamp

    @staticmethod
    def _revalidate(request: tuple[str, str]) -> bool:
        if not settings.LLM_RESPONSE_CACHE_ENABLED:
            raise PermissionError("缓存生成开关已变更，请重新发起请求")
        stamp = current_cache_snapshot()
        if stamp != request[1]:
            raise PermissionError("缓存生成请求上下文已变更，请重新发起请求")
        return True

    async def chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> str:
        opts = (options or LLMOptions()).model_copy(deep=True)
        self._guard(messages, opts)
        payload = copy.deepcopy(self._payload(messages, opts, False))
        headers = dict(self._headers())
        url = f"{self.base_url}/chat/completions"
        timeout = opts.timeout or self.timeout
        request = self._cache_request(payload, opts, headers, url, timeout)
        if request is not None:
            cached = _responses.get(request[0])
            if isinstance(cached, str) and cached.strip() and self._revalidate(request):
                return cached
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                raise LLMResponseError("llm_invalid_response") from None
        content = _response_content(data)
        choice = data["choices"][0]
        if request is not None and self._revalidate(request):
            if (data.get("model") == payload["model"] and choice.get("finish_reason") == "stop"
                    and isinstance(content, str) and content and not choice["message"].get("tool_calls")
                    and not choice["message"].get("function_call")):
                _responses.put(request[0], content)
        return content

    async def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        opts = (options or LLMOptions()).model_copy(deep=True)
        self._guard(messages, opts)
        payload = copy.deepcopy(self._payload(messages, opts, True))
        headers = dict(self._headers())
        url = f"{self.base_url}/chat/completions"
        timeout = opts.timeout or self.timeout
        request = self._cache_request(payload, opts, headers, url, timeout)
        if request is not None:
            cached = _responses.get(request[0])
            if isinstance(cached, list):
                for content in cached:
                    if not self._revalidate(request):
                        break
                    yield content
                else:
                    return
        parts: list[str] = []
        complete = False
        stopped = False
        reusable = request is not None
        size = 0
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if data_str == "[DONE]":
                        complete = True
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        reusable = False
                        continue
                    choice = chunk.get("choices", [{}])[0]
                    delta = choice.get("delta", {})
                    if (chunk.get("model") != payload["model"] or delta.get("tool_calls")
                            or delta.get("function_call")):
                        reusable = False
                    if choice.get("finish_reason") == "stop":
                        stopped = True
                    elif choice.get("finish_reason") is not None:
                        reusable = False
                    content = delta.get("content")
                    if content:
                        if request is not None:
                            self._revalidate(request)
                        size += len(content.encode())
                        if size > settings.CACHE_MAX_ENTRY_BYTES:
                            reusable = False
                            parts.clear()
                        if reusable:
                            parts.append(content)
                        yield content
        if request is not None and reusable and complete and stopped and parts and self._revalidate(request):
            _responses.put(request[0], parts)
