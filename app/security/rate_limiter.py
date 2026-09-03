"""速率限制器 — Token Bucket 算法。

按用户/租户/IP 维度限制请求频率，防止滥用。

当前实现：内存级 Token Bucket，不跨实例共享。可按需扩展：
- Redis 分布式限流（多实例共享）
- 滑动窗口算法（更平滑的限流曲线）
- 熔断器（LLM 错误率过高时自动降级）
"""

import time
from dataclasses import dataclass, field
from threading import Lock

from app.security.types import SecurityContext


@dataclass
class _Bucket:
    """Token Bucket 内部状态。"""

    tokens: float = 0.0
    last_refill: float = 0.0


@dataclass
class RateLimitConfig:
    """速率限制配置。"""

    rate: float = 60.0  # 每秒补充的 token 数
    capacity: float = 60.0  # 桶容量（最大积压 token 数）
    enabled: bool = True


class RateLimiter:
    """Token Bucket 速率限制器。

    使用方式::

        limiter = RateLimiter(RateLimitConfig(rate=10, capacity=20))
        allowed, remaining = limiter.allow("user-123")
        if not allowed:
            raise HTTPException(429, "请求过于频繁")
    """

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or RateLimitConfig()
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()

    def allow(self, key: str, cost: float = 1.0, ctx: SecurityContext | None = None) -> tuple[bool, int]:
        """检查是否允许本次请求。

        Args:
            key: 限流键（如 user_id:tenant_id）。
            cost: 本次请求消耗的 token 数（默认 1）。
            ctx: 可选的安全上下文。

        Returns:
            (是否允许, 剩余 token 数)。
        """
        if not self._config.enabled:
            return True, -1

        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                # 新桶必须满桶启动。初始 token 为 0 时，键的首次请求会被误判为超限；
                # 补充量又取决于进程已运行时长，会让「首请求能否通过」变得不可预测。
                bucket = _Bucket(tokens=self._config.capacity, last_refill=now)
                self._buckets[key] = bucket
                elapsed = 0.0
            else:
                # 补充 token
                elapsed = now - bucket.last_refill
            bucket.tokens = min(self._config.capacity, bucket.tokens + elapsed * self._config.rate)
            bucket.last_refill = now

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                remaining = int(bucket.tokens)
                if ctx is not None:
                    ctx.rate_limit_remaining = remaining
                return True, remaining

            # 拒绝
            remaining = int(bucket.tokens)
            if ctx is not None:
                ctx.rate_limited = True
                ctx.rate_limit_remaining = remaining
            return False, remaining

    def reset(self, key: str) -> None:
        """重置指定键的桶（用于测试）。"""
        with self._lock:
            self._buckets.pop(key, None)

    def reset_all(self) -> None:
        """重置所有桶（用于测试）。"""
        with self._lock:
            self._buckets.clear()