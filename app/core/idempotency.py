"""单进程幂等键：同一应用进程内重复提交只保留第一次结果。

不跨实例共享；键过期后允许再次提交。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

_DEFAULT_TTL_SECONDS = 24 * 60 * 60


@dataclass
class _Entry:
    value: Any
    expires_at: float


class ProcessIdempotencyStore:
    """进程内幂等结果缓存。"""

    def __init__(self, *, ttl_seconds: float = _DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                del self._entries[key]
                return None
            return entry.value

    def put_if_absent(self, key: str, value: Any) -> Any:
        """若键已存在则返回旧值，否则写入并返回新值。"""
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > now:
                return entry.value
            self._entries[key] = _Entry(value=value, expires_at=now + self._ttl)
            return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


# 导入任务批次：键 = tenant_id:user_id:Idempotency-Key
import_batch_keys = ProcessIdempotencyStore()

# 租户切换：键 = user_id:tenant_id → 最近一次 token 载荷摘要（可选复用）
switch_tenant_keys = ProcessIdempotencyStore(ttl_seconds=60)
