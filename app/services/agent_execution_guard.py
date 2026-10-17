"""Single-process exclusion for simultaneous execution of the same message."""

import functools
import threading
from collections.abc import Callable
from typing import Any


class ResumeRejectedError(ValueError):
    """Safe public error for unavailable or unauthorized recovery."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


_active: set[tuple[int, str, str, str]] = set()
_lock = threading.Lock()


def claim_execution(session: Any, tenant_id: str, conversation_id: str, message_id: str,
                    claims: list[tuple[int, str, str, str]]) -> None:
    key = (id(session.get_bind()), tenant_id, conversation_id, message_id)
    with _lock:
        if key in _active:
            raise ResumeRejectedError("该消息正在执行，请稍后重试")
        _active.add(key)
    claims.append(key)


def release_executions(claims: list[tuple[int, str, str, str]]) -> None:
    with _lock:
        for key in claims:
            _active.discard(key)


def guarded_chat(method: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(method)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        claims: list[tuple[int, str, str, str]] = []
        try:
            return await method(*args, **kwargs, _execution_claims=claims)
        finally:
            release_executions(claims)
    return wrapped


def guarded_stream(method: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(method)
    async def wrapped(*args: Any, **kwargs: Any):
        claims: list[tuple[int, str, str, str]] = []
        iterator = method(*args, **kwargs, _execution_claims=claims)
        try:
            async for event in iterator:
                yield event
        finally:
            try:
                await iterator.aclose()
            finally:
                release_executions(claims)
    return wrapped
