"""安全治理模块。

提供：
- 输入过滤：PII 检测 + 敏感词过滤
- 输出过滤：有害内容检测 + 敏感信息遮蔽
- Prompt 注入检测：已知注入模式匹配
- 日志脱敏：敏感字段自动遮蔽
- 速率限制：Token Bucket 算法

对外暴露：
- ``InputFilter`` / ``OutputFilter``：输入/输出过滤器
- ``PromptInjectionDetector``：注入检测器
- ``LogSanitizer``：日志脱敏器
- ``RateLimiter``：速率限制器
- ``SecurityContext``：安全上下文（贯穿一次请求的过滤结果）
- ``get_rate_limiter`` / ``get_log_sanitizer``：进程级单例（限流必须跨请求共享桶状态）
"""

from app.core.config import settings
from app.security.input_filter import InputFilter, InputFilterResult
from app.security.log_sanitizer import LogSanitizer
from app.security.output_filter import OutputFilter, OutputFilterResult
from app.security.prompt_injection import (
    InjectionResult,
    PromptInjectionDetector,
)
from app.security.rate_limiter import RateLimitConfig, RateLimiter
from app.security.types import SecurityContext, SecurityRejectedError

_rate_limiter: RateLimiter | None = None
_log_sanitizer: LogSanitizer | None = None


def get_rate_limiter() -> RateLimiter:
    """返回全局速率限制器单例。

    限流器必须跨请求共享：每次请求新建实例会让桶状态归零，限流形同虚设。
    """
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(
            RateLimitConfig(
                rate=settings.SECURITY_RATE_LIMIT_RATE,
                capacity=settings.SECURITY_RATE_LIMIT_CAPACITY,
                enabled=settings.SECURITY_RATE_LIMIT,
            )
        )
    return _rate_limiter


def get_log_sanitizer() -> LogSanitizer:
    """返回全局日志脱敏器单例。"""
    global _log_sanitizer
    if _log_sanitizer is None:
        _log_sanitizer = LogSanitizer()
    return _log_sanitizer


def reset_security_singletons() -> None:
    """重置单例（仅用于测试与配置变更后重建）。"""
    global _rate_limiter, _log_sanitizer
    _rate_limiter = None
    _log_sanitizer = None


__all__ = [
    "InputFilter",
    "InputFilterResult",
    "OutputFilter",
    "OutputFilterResult",
    "PromptInjectionDetector",
    "InjectionResult",
    "LogSanitizer",
    "RateLimiter",
    "RateLimitConfig",
    "SecurityContext",
    "SecurityRejectedError",
    "get_rate_limiter",
    "get_log_sanitizer",
    "reset_security_singletons",
]