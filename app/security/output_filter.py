"""输出过滤器 — 有害内容检测 + 敏感信息遮蔽。

基于正则表达式的模式匹配，检测模型输出中的不安全内容。

当前实现：仅基础模式匹配。可按需扩展：
- 基于 LLM 的内容安全审查（二次审查）
- 敏感信息遮蔽（PII 自动替换为占位符）
- 可配置的安全策略（告警 vs 阻断）
"""

import re
from dataclasses import dataclass, field

from app.security.types import SecurityContext

# ── 有害内容模式 ──

_HARMFUL_PATTERNS: list[tuple[str, re.Pattern]] = [
    # 暴力/自残内容
    ("violence", re.compile(
        r"(?i)(自杀|自残|杀人|武器制作|爆炸物)"
    )),
    # 违法内容
    ("illegal", re.compile(
        r"(?i)(黑客.*攻击|破解.*密码|入侵.*系统|DDoS)"
    )),
    # 隐私泄露信号（模型输出中包含疑似 PII）
    ("pii_leak", re.compile(
        r"\b\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b|"
        r"\b1[3-9]\d{9}\b|"
        r"\b\d{16,19}\b"
    )),
    # 越狱/提示词泄露
    ("jailbreak", re.compile(
        r"(?i)(忽略.*指令|忘记.*规则|system\s*prompt|you are now|DAN)"
    )),
]


@dataclass
class OutputFilterResult:
    """输出过滤结果。"""

    matches: list[str] = field(default_factory=list)  # 匹配到的有害内容类型
    flagged: bool = False
    reasons: list[str] = field(default_factory=list)
    # 脱敏后的文本
    sanitized_text: str = ""


class OutputFilter:
    """输出过滤器。

    使用方式::

        filt = OutputFilter()
        result = filt.filter(model_output)
        if result.flagged:
            print("检测到有害内容:", result.reasons)
    """

    def __init__(self, *, block_on_flag: bool = False) -> None:
        """初始化输出过滤器。

        Args:
            block_on_flag: 检测到有害内容时是否阻断输出（默认仅告警）。
        """
        self._block_on_flag = block_on_flag

    def filter(self, text: str, ctx: SecurityContext | None = None) -> OutputFilterResult:
        """检测模型输出中的有害内容。

        Args:
            text: 模型输出文本。
            ctx: 可选的安全上下文。

        Returns:
            OutputFilterResult：包含检测结果和原因。
        """
        result = OutputFilterResult(sanitized_text=text)

        for label, pattern in _HARMFUL_PATTERNS:
            if pattern.search(text):
                result.matches.append(label)
                result.reasons.append(f"检测到 {label} 内容")

        result.flagged = bool(result.matches)

        if ctx is not None:
            ctx.output_flagged = result.flagged
            ctx.output_reasons = result.reasons

        return result