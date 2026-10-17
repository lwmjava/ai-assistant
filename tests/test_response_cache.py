"""Bounded cache counterexamples; integration acceptance lives in a separate suite."""

import math

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.core.response_cache import BoundedCache, bind_cache_scope, current_cache_snapshot


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, 1e300, 0, -1, True])
def test_cache_invalid_runtime_ttl_cannot_replay(value, monkeypatch):
    cache = BoundedCache()
    cache.put("key", "private")
    monkeypatch.setattr(settings, "CACHE_TTL_SECONDS", value)
    assert cache.get("key") is None
    cache.put("key", "private")
    assert cache.get("key") is None


@pytest.mark.parametrize("name,value", [
    ("CACHE_MAX_ENTRIES", True), ("CACHE_MAX_ENTRIES", 1e300),
    ("CACHE_MAX_BYTES", math.nan), ("CACHE_MAX_ENTRY_BYTES", -1),
    ("CACHE_MAX_ENTRY_BYTES", 100 * 1024 * 1024),
])
def test_cache_invalid_runtime_capacity_cannot_replay(name, value, monkeypatch):
    cache = BoundedCache()
    cache.put("key", "private")
    monkeypatch.setattr(settings, name, value)
    assert cache.get("key") is None


@pytest.mark.parametrize("name,value", [
    ("CACHE_TTL_SECONDS", math.nan), ("CACHE_TTL_SECONDS", math.inf),
    ("CACHE_TTL_SECONDS", True), ("CACHE_MAX_ENTRIES", True),
    ("CACHE_MAX_BYTES", -1), ("CACHE_MAX_ENTRY_BYTES", 100 * 1024 * 1024),
])
def test_cache_invalid_configuration_rejected(name, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, EMBEDDING_PROVIDER="mock", **{name: value})


def test_cache_lru_ttl_size_and_copy_boundaries(monkeypatch):
    monkeypatch.setattr(settings, "CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(settings, "CACHE_MAX_BYTES", 128)
    monkeypatch.setattr(settings, "CACHE_MAX_ENTRY_BYTES", 64)
    monkeypatch.setattr(settings, "CACHE_TTL_SECONDS", 3.0)
    clock = [10.0]
    monkeypatch.setattr("app.core.response_cache.time.monotonic", lambda: clock[0])
    cache = BoundedCache()
    payload = ["original"]
    cache.put("a", payload)
    payload[0] = "mutated"
    found = cache.get("a")
    found[0] = "mutated"
    assert cache.get("a") == ["original"]
    cache.put("b", "b")
    cache.get("a")
    cache.put("c", "c")
    assert cache.get("b") is None
    cache.put("oversize", "x" * 128)
    assert cache.get("oversize") is None
    clock[0] = 13.0
    assert cache.get("a") is None
    assert cache.get("c") is None


def test_cache_closed_and_nested_request_leases_cannot_replay():
    assert current_cache_snapshot() is None
    with bind_cache_scope(lambda: "outer") as lease:
        with bind_cache_scope(lambda: "inner"):
            assert current_cache_snapshot() == "inner"
        assert current_cache_snapshot() == "outer"
    assert lease.snapshot() is None
    assert current_cache_snapshot() is None
