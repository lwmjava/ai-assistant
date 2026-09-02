"""安全治理共享类型。

定义安全上下文：贯穿一次请求的过滤结果，供各层过滤器协同使用。
"""

from dataclasses import dataclass, field


@dataclass
class SecurityContext:
    """一次请求的安全上下文。

    由各过滤器按顺序填充，供后续阶段（如审计日志）读取。
    """

    # 输入过滤结果
    input_flagged: bool = False
    input_reasons: list[str] = field(default_factory=list)
    input_pii_detected: list[str] = field(default_factory=list)  # 检测到的 PII 类型列表

    # 注入检测结果
    injection_detected: bool = False
    injection_confidence: float = 0.0
    injection_reasons: list[str] = field(default_factory=list)

    # 输出过滤结果
    output_flagged: bool = False
    output_reasons: list[str] = field(default_factory=list)

    # 速率限制
    rate_limited: bool = False
    rate_limit_remaining: int = -1

    @property
    def blocked(self) -> bool:
        """是否应阻断本次请求。"""
        return self.input_flagged or self.output_flagged or self.rate_limited