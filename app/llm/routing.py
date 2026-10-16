"""按用途把一次模型调用交给一条兜底链。

换家只发生在这里。理解、规划、行动、反思和响应不各自决定下一家。
"""

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.llm.base import ChatMessage, LLMOptions, LLMProvider

logger = logging.getLogger(__name__)

UNAVAILABLE_MESSAGE = "当前没有可用的模型，请检查环境变量中的模型密钥后重试。"

_FAILOVER_STATUS = {401, 403, 408, 429}
MAX_CIRCUITS = 256
CIRCUIT_IDLE_TTL_SECONDS = 600.0


def _transient_failure(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 429} or exc.response.status_code >= 500
    return isinstance(exc, httpx.TimeoutException | httpx.RequestError)


@dataclass
class _CircuitState:
    failures: int = 0
    opened_until: float = 0.0
    epoch: int = 0
    probe: bool = False
    inflight: int = 0
    touched: float = 0.0


@dataclass(frozen=True)
class _Lease:
    key: str
    state: _CircuitState
    epoch: int
    probe: bool
    threshold: int
    cooldown: float


class _CircuitRegistry:
    def __init__(self) -> None:
        self.states: OrderedDict[str, _CircuitState] = OrderedDict()
        self.lock = threading.Lock()

    def reset(self) -> None:
        with self.lock:
            self.states.clear()

    @staticmethod
    def _key(task: str, profile: str, provider: LLMProvider) -> str:
        secret = str(getattr(provider, "api_key", ""))
        credential = hashlib.sha256(secret.encode()).hexdigest()
        identity = (task, profile, type(provider).__module__, type(provider).__qualname__,
                    getattr(provider, "base_url", ""), provider.model, credential,
                    getattr(provider, "timeout", None), settings.LLM_CIRCUIT_FAILURE_THRESHOLD,
                    settings.LLM_CIRCUIT_COOLDOWN_SECONDS)
        return hashlib.sha256(repr(identity).encode()).hexdigest()

    def acquire(self, task: str, profile: str, provider: LLMProvider) -> _Lease | None:
        key = self._key(task, profile, provider)
        now = time.monotonic()
        with self.lock:
            lifetime = max(CIRCUIT_IDLE_TTL_SECONDS, settings.LLM_CIRCUIT_COOLDOWN_SECONDS * 2)
            for old_key, old in list(self.states.items()):
                if not old.inflight and now - old.touched > lifetime:
                    del self.states[old_key]
            state = self.states.get(key)
            if state is None:
                if len(self.states) >= MAX_CIRCUITS:
                    evict = next((name for name, item in self.states.items() if not item.inflight), None)
                    if evict is None:
                        return None
                    del self.states[evict]
                state = _CircuitState(touched=now)
                self.states[key] = state
            self.states.move_to_end(key)
            state.touched = now
            if state.opened_until > now:
                return None
            probe = state.opened_until != 0.0
            if probe and state.probe:
                return None
            if probe:
                state.probe = True
            state.inflight += 1
            return _Lease(key, state, state.epoch, probe,
                          settings.LLM_CIRCUIT_FAILURE_THRESHOLD, settings.LLM_CIRCUIT_COOLDOWN_SECONDS)

    def finish(self, lease: _Lease, outcome: str) -> None:
        with self.lock:
            state = self.states.get(lease.key)
            if state is not lease.state:
                return
            state.inflight -= 1
            state.touched = time.monotonic()
            if state.epoch != lease.epoch:
                return
            if outcome == "success":
                state.failures = 0
                if lease.probe:
                    state.opened_until = 0.0
                    state.probe = False
                    state.epoch += 1
            elif outcome == "failure":
                state.failures += 1
                if lease.probe or state.failures >= lease.threshold:
                    state.opened_until = state.touched + lease.cooldown
                    state.probe = False
                    state.epoch += 1
            elif lease.probe:
                state.probe = False
                state.epoch += 1


_circuits = _CircuitRegistry()


def reset_circuits() -> None:
    """显式重置进程熔断状态；测试与 provider override 生命周期复用。"""
    _circuits.reset()


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
    """按顺序尝试可用配置；共享熔断登记跨工厂实例保持短期故障状态。"""

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
            lease = _circuits.acquire(self.task, name, provider)
            if lease is None:
                continue
            outcome = "neutral"
            try:
                text = await provider.chat(messages, options)
            except Exception as exc:
                if _transient_failure(exc):
                    outcome = "failure"
                if not is_failover_error(exc):
                    raise
                self._log_failure(name, exc)
                continue
            else:
                outcome = "success"
            finally:
                _circuits.finish(lease, outcome)
            self.model = provider.model
            return text
        raise LLMUnavailableError()

    async def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        if not self.hops:
            raise LLMUnavailableError()
        for name, provider in self.hops:
            lease = _circuits.acquire(self.task, name, provider)
            if lease is None:
                continue
            yielded = False
            outcome = "neutral"
            stream = None
            try:
                stream = provider.stream_chat(messages, options)
                async for delta in stream:
                    yielded = True
                    yield delta
            except Exception as exc:
                if _transient_failure(exc):
                    outcome = "failure"
                if yielded or not is_failover_error(exc):
                    if yielded:
                        raise LLMUnavailableError() from None
                    raise
                self._log_failure(name, exc)
                continue
            else:
                outcome = "success"
            finally:
                try:
                    close = getattr(stream, "aclose", None) if stream is not None else None
                    if close is not None:
                        await close()
                finally:
                    _circuits.finish(lease, outcome)
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
