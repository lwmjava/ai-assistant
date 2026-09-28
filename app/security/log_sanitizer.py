"""日志脱敏器 — 自动遮蔽日志中的敏感字段。

在日志输出前自动替换敏感字段值（token、password、key 等）为占位符。

使用方式::

    sanitizer = LogSanitizer()
    safe = sanitizer.sanitize('{"token": "abc123", "user": "alice"}')
    # '{"token": "***", "user": "alice"}'
"""

import re

# ── 敏感字段名模式 ──

_SENSITIVE_FIELDS: list[str] = [
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "credential",
    "private_key",
    "access_key",
    "jwt",
    "bearer",
]

# 字段值：引号内可含空格；无引号时连续词（含空格）算同一段，在逗号、&、}、引号或换行处停下。
_FIELD_ALTERNATION = "|".join(_SENSITIVE_FIELDS)
_SENSITIVE_KEY_PATTERN = re.compile(
    rf"(?i)([\"']?(?:{_FIELD_ALTERNATION})[\"']?\s*[:=]\s*)"
    r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[^\s\"',}&]+(?:[ \t]+[^\s\"',}&]+)*)"
)

# 敏感值模式（独立于字段名，作为兜底）
_SENSITIVE_VALUE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # JWT token 格式
    ("jwt_token", re.compile(
        r'(?:Bearer\s+)?eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'
    )),
    # API Key 模式
    ("api_key_value", re.compile(
        r'\b(sk-[A-Za-z0-9_-]{20,}|api-[A-Za-z0-9_-]{20,}|key-[A-Za-z0-9_-]{20,})\b'
    )),
]


class LogSanitizer:
    """日志脱敏器。

    在日志输出前调用，自动替换敏感字段值。
    """

    def __init__(self, *, mask_char: str = "***") -> None:
        self._mask = mask_char

    def _mask_sensitive_field(self, match: re.Match[str]) -> str:
        """保留字段名和引号，把整段值换成占位符。"""
        prefix = match.group(1)
        value = match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return f"{prefix}{value[0]}{self._mask}{value[-1]}"
        return f"{prefix}{self._mask}"

    def sanitize(self, text: str) -> str:
        """脱敏文本中的敏感信息。

        Args:
            text: 原始文本（日志消息、JSON 字符串等）。

        Returns:
            脱敏后的文本。
        """
        # 1. 按字段名脱敏
        text = _SENSITIVE_KEY_PATTERN.sub(self._mask_sensitive_field, text)

        # 2. 按值模式脱敏（兜底）
        for _label, pattern in _SENSITIVE_VALUE_PATTERNS:
            text = pattern.sub(self._mask, text)

        return text
