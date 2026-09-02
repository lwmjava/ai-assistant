"""输入过滤器 — PII 检测 + 敏感词过滤。

基于正则表达式的模式匹配，无需外部 NLP 依赖。
检测类型：身份证号、手机号、银行卡号、邮箱、IP 地址、API Key 模式。

骨架阶段仅支持正则匹配；内核打磨阶段补充：
- 基于 NLP 的语义敏感内容检测
- 可配置的黑名单词库（YAML/JSON 文件）
- 规则热重载（文件变更自动生效）
"""

import re
from dataclasses import dataclass, field

from app.security.types import SecurityContext

# ── PII 正则模式 ──

_PII_PATTERNS: dict[str, re.Pattern] = {
    # 中国大陆身份证号（18 位，含校验位 X）
    "id_card": re.compile(
        r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"
    ),
    # 中国大陆手机号（1 开头，第二位 3-9）
    "phone": re.compile(
        r"\b1[3-9]\d{9}\b"
    ),
    # 银行卡号（16-19 位数字）
    "bank_card": re.compile(
        r"\b\d{16,19}\b"
    ),
    # 邮箱
    "email": re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    ),
    # IPv4 地址
    "ip_address": re.compile(
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
    ),
    # API Key 模式（常见前缀：sk-、api-、key-）
    "api_key": re.compile(
        r"\b(sk-[A-Za-z0-9_-]{20,}|api-[A-Za-z0-9_-]{20,}|key-[A-Za-z0-9_-]{20,})\b"
    ),
}

# ── 敏感词模式（基础）──

_SENSITIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # SQL 注入探测
    ("sql_injection", re.compile(
        r"(?i)(\bselect\b.*\bfrom\b|\binsert\b.*\binto\b|\bdelete\b.*\bfrom\b|\bdrop\b.*\btable\b|\bunion\b.*\bselect\b)"
    )),
    # 系统命令注入
    ("command_injection", re.compile(
        r"(?i)(\brm\s+-rf\b|\b/.*/passwd\b|\b/etc/shadow\b|\bcurl\b.*\bpipe\b|\bwget\b.*\b-O\b)"
    )),
    # XSS 探测
    ("xss_attempt", re.compile(
        r"(?i)(<script\b|javascript:|onerror\s*=|onload\s*=|<\/?iframe\b)"
    )),
]


@dataclass
class InputFilterResult:
    """输入过滤结果。"""

    pii_detected: list[str] = field(default_factory=list)  # 检测到的 PII 类型
    sensitive_matches: list[str] = field(default_factory=list)  # 敏感词匹配类型
    flagged: bool = False  # 是否标记为异常
    reasons: list[str] = field(default_factory=list)  # 标记原因
    # 脱敏后的文本（PII 替换为 ***）
    sanitized_text: str = ""


class InputFilter:
    """输入过滤器。

    使用方式::

        filt = InputFilter()
        result = filt.filter("我的手机号是 13800138000")
        if result.flagged:
            print("检测到敏感信息:", result.reasons)
        safe_text = result.sanitized_text  # "我的手机号是 ***"
    """

    def __init__(self, *, detect_pii: bool = True, detect_sensitive: bool = True) -> None:
        self._detect_pii = detect_pii
        self._detect_sensitive = detect_sensitive

    def filter(self, text: str, ctx: SecurityContext | None = None) -> InputFilterResult:
        """检测并脱敏输入文本。

        Args:
            text: 待检测的输入文本。
            ctx: 可选的安全上下文，结果会回填其中。

        Returns:
            InputFilterResult：包含检测结果和脱敏后文本。
        """
        result = InputFilterResult(sanitized_text=text)

        # PII 检测
        if self._detect_pii:
            for pii_type, pattern in _PII_PATTERNS.items():
                match = pattern.search(text)
                if match:
                    result.pii_detected.append(pii_type)
                    result.reasons.append(f"检测到疑似 {pii_type}")
                    # 脱敏：替换匹配内容
                    result.sanitized_text = pattern.sub("***", result.sanitized_text)

        # 敏感词检测
        if self._detect_sensitive:
            for label, pattern in _SENSITIVE_PATTERNS:
                if pattern.search(text):
                    result.sensitive_matches.append(label)
                    result.reasons.append(f"检测到 {label} 模式")

        result.flagged = bool(result.pii_detected or result.sensitive_matches)

        # 回填安全上下文
        if ctx is not None:
            ctx.input_flagged = result.flagged
            ctx.input_reasons = result.reasons
            ctx.input_pii_detected = result.pii_detected

        return result