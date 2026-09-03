"""安全治理共享类型。

定义安全上下文：贯穿一次请求的过滤结果，供各层过滤器协同使用。
"""

from dataclasses import dataclass, field

from app.core.config import settings


class SecurityRejectedError(ValueError):
    """请求被安全策略拒绝。

    继承 ``ValueError`` 以兼容既有调用方，同时携带 ``status_code`` 让路由层
    能区分「限流」与「内容阻断」，而不是一律把安全拒绝报成 404。
    """

    def __init__(self, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


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
        """是否应阻断本次请求。

        注入检测是否阻断由 ``SECURITY_BLOCK_ON_INJECTION`` 单独控制：
        该开关默认关闭（仅告警），因此此处不能无条件把 ``injection_detected``
        计入阻断，否则线上默认行为会从「告警」突变为「拒绝服务」。
        """
        if self.input_flagged or self.output_flagged or self.rate_limited:
            return True
        return self.injection_detected and settings.SECURITY_BLOCK_ON_INJECTION