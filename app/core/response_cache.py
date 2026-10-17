"""Bounded process cache and request leases; no database or provider dependency."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import time
import uuid
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                    ensure_ascii=False).encode()).hexdigest()


_namespaces: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()
_namespace_lock = threading.RLock()


def object_namespace(obj: object) -> str:
    """Engine/implementation objects have unique lifetimes, not recycled id() keys."""
    with _namespace_lock:
        if obj not in _namespaces:
            _namespaces[obj] = uuid.uuid4().hex
        return _namespaces[obj]


_source_state: tuple[object, str] | None = None


def configuration_stamp() -> str:
    """Settings and actual application/Prompt source version, stored only as a digest."""
    global _source_state
    root = Path(__file__).resolve().parents[1]
    files = sorted(root.rglob("*.py"))
    stat = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    if _source_state is None or _source_state[0] != stat:
        _source_state = (stat, digest([(str(p.relative_to(root)),
                                      hashlib.sha256(p.read_bytes()).hexdigest()) for p in files]))
    return digest((settings.model_dump(), _source_state[1]))


def cache_limits() -> tuple[float, int, int, int] | None:
    values = (settings.CACHE_TTL_SECONDS, settings.CACHE_MAX_ENTRIES,
              settings.CACHE_MAX_BYTES, settings.CACHE_MAX_ENTRY_BYTES)
    if any(isinstance(v, bool) or not isinstance(v, float | int)
           or v <= 0 or v > 64 * 1024 * 1024 or not math.isfinite(v) for v in values):
        return None
    ttl, entries, total, item = values
    if ttl > 3600 or entries > 10000 or total > 64 * 1024 * 1024 or item > total:
        return None
    if any(not isinstance(v, int) for v in (entries, total, item)):
        return None
    return float(ttl), int(entries), int(total), int(item)


class BoundedCache:
    """LRU+TTL; independent copies, finite global byte and item limits per cache."""

    def __init__(self) -> None:
        self._items: OrderedDict[str, tuple[float, object, int]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()
        self._limits: tuple[float, int, int, int] | None = None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def _prepare(self) -> tuple[float, int, int, int] | None:
        limits = cache_limits()
        if limits != self._limits:
            self.clear()
            self._limits = limits
        now = time.monotonic()
        for key, (expires, _, size) in list(self._items.items()):
            if now >= expires:
                del self._items[key]
                self._bytes -= size
        return limits

    def get(self, key: str) -> Any | None:
        with self._lock:
            if self._prepare() is None:
                return None
            item = self._items.get(key)
            if item is None:
                return None
            self._items.move_to_end(key)
            return copy.deepcopy(item[1])

    def put(self, key: str, value: object) -> None:
        with self._lock:
            limits = self._prepare()
            if limits is None:
                return
            size = len(json.dumps(value, default=str, ensure_ascii=False).encode()) + len(key)
            ttl, entries, total, item_limit = limits
            if size > item_limit:
                return
            if key in self._items:
                self._bytes -= self._items.pop(key)[2]
            while self._items and (len(self._items) >= entries or self._bytes + size > total):
                self._bytes -= self._items.popitem(last=False)[1][2]
            self._items[key] = (time.monotonic() + ttl, copy.deepcopy(value), size)
            self._bytes += size


@dataclass
class CacheLease:
    callback: Callable[[], str | None]
    active: bool = True

    def snapshot(self) -> str | None:
        return self.callback() if self.active else None


_lease: ContextVar[CacheLease | None] = ContextVar("response_cache_lease", default=None)


@contextmanager
def bind_cache_scope(callback: Callable[[], str | None]) -> Iterator[CacheLease]:
    lease = CacheLease(callback)
    token = _lease.set(lease)
    try:
        yield lease
    finally:
        lease.active = False  # Tasks that inherited this lease cannot reuse a closed Session.
        _lease.reset(token)


def current_cache_snapshot() -> str | None:
    lease = _lease.get()
    return lease.snapshot() if lease is not None else None
