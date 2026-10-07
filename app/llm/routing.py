"""按用途把一次模型调用交给一条兜底链。

换家只发生在这里。理解、规划、行动、反思和响应不各自决定下一家。
"""

import logging
from collections.abc import AsyncIterator

import httpx

from app.llm.base import ChatMessage, LLMOptions, LLMProvider

logger = logging.getLogger(__name__)

UNAVAILABLE_MESSAGE = "当前没有可用的模型，请检查环境变量中的模型密钥后重试。"

_FAILOVER_STATUS = {401, 403, 408, 429}


class LLMUnavailableError(RuntimeError):
    """整条兜底链都没有给出可用结果。消息固定，不含密钥或供应商原文。"""

    def __init__(self) -> None:
        super().__init__(UNAVAILABLE_MESSAGE)


def is_failover_error(exc: BaseException) -> bool:
    """超时、连不上，以及 401/403/408/429/5xx 才换下一家。400 不换。

    预算超限（``ContextBudgetError``）既不是网络错误也不是对方的状态码，
    换家重试只会拿另一家的窗口再判一次同样的 payload，属于白跑，因此不换。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code in _FAILOVER_STATUS or code >= 500
    return isinstance(exc, httpx.TimeoutException | httpx.RequestError)


class FallbackChain(LLMProvider):
    """按顺序尝试多家。每一跳失败后从头再来，不记住上一轮换到了谁。"""

    def __init__(self, task: str, hops: list[tuple[str, LLMProvider]]) -> None:
        self.task = task
        self.hops = hops
        self.model = hops[0][1].model if hops else "unavailable"

    async def chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> str:
        if not self.hops:
            raise LLMUnavailableError()
        for name, provider in self.hops:
            try:
                text = await provider.chat(messages, options)
            except Exception as exc:
                if not is_failover_error(exc):
                    raise
                self._log_failure(name, exc)
                continue
            self.model = provider.model
            return text
        raise LLMUnavailableError()

    async def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        if not self.hops:
            raise LLMUnavailableError()
        for name, provider in self.hops:
            yielded = False
            try:
                async for delta in provider.stream_chat(messages, options):
                    yielded = True
                    yield delta
            except Exception as exc:
                if yielded or not is_failover_error(exc):
                    if yielded:
                        raise LLMUnavailableError() from None
                    raise
                self._log_failure(name, exc)
                continue
            self.model = provider.model
            return
        raise LLMUnavailableError()

    def _log_failure(self, profile: str, exc: BaseException) -> None:
        status: int | str = "-"
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
        logger.warning(
            "模型调用失败 task=%s profile=%s error=%s status=%s",
            self.task,
            profile,
            type(exc).__name__,
            status,
        )
