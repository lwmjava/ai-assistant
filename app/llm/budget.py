"""每次真实调用的上下文预算核对。

护栏只认一条不等式：

    实际 payload + 输出预留 + 余量 ≤ 已验证上下文窗口

payload 用**真正要发的 messages** 现算，因此工具返回、自纠错（critique）轮次、
历史与系统提示都天然被计入，不需要每个调用方各自统计一遍。

异常消息只含计数数字与原因码，绝不含 prompt / 工具返回原文——本仓库有日志脱敏
规范，异常会顺着 logger.exception 进日志，也会被调用方转成面向用户的提示。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.llm.capabilities import GenerationCapability
from app.llm.counters import PayloadUncountableError

logger = logging.getLogger(__name__)

# 面向用户的固定提示。异常原文、模型名、payload 片段都不给出去。
BUDGET_EXCEEDED_MESSAGE = "本次请求内容超出模型已登记的上下文预算，请缩短输入或拆分后重试。"

CAPABILITY_UNVERIFIED = "capability_unverified"
CONTEXT_WINDOW_EXCEEDED = "context_window_exceeded"
OUTPUT_RESERVE_EXCEEDED = "output_reserve_exceeded"
# 计不出来（多模态内容无法序列化、正文含不可编码字符）：不知道会不会超限，就不发。
PAYLOAD_UNCOUNTABLE = "payload_uncountable"


class ContextBudgetError(RuntimeError):
    """请求超出已登记的上下文预算。

    属性里只有数字与原因码：不持有 messages、不持有工具返回原文。
    """

    def __init__(
        self,
        reason: str,
        *,
        payload_tokens: int,
        reserved_output: int,
        margin: int,
        limit: int,
    ) -> None:
        super().__init__(
            f"context_budget_exceeded reason={reason} "
            f"payload={payload_tokens} reserved={reserved_output} margin={margin} limit={limit}"
        )
        self.reason = reason
        self.payload_tokens = payload_tokens
        self.reserved_output = reserved_output
        self.margin = margin
        self.limit = limit


def estimate_payload_tokens(
    capability: GenerationCapability | None, messages: Sequence[Any]
) -> int | None:
    """按能力登记的计数器算 payload。

    没有能力（无批准配置）时返回 0；计数器算不出来时返回 None（= 无法计数）。
    """
    if capability is None:
        return 0
    try:
        return int(capability.counter(messages))
    except PayloadUncountableError:
        return None


def check_generation_payload(
    capability: GenerationCapability | None,
    messages: Sequence[Any],
    reserved_output: int,
    margin: int,
) -> str | None:
    """返回原因码，通过返回 None。

    - ``capability_unverified``：没有已批准的能力配置（未知模型且运营者未声明）；
    - ``output_reserve_exceeded``：输出预留超过该模型的最大输出；
    - ``payload_uncountable``：payload 计不出来（多模态内容 / 不可编码字符）；
    - ``context_window_exceeded``：payload + 输出预留 + 余量 超过已验证窗口。

    输出预留先判：它是配置问题，与 payload 长不长无关，先判能给出更准的原因码。
    """
    if capability is None:
        return CAPABILITY_UNVERIFIED
    if reserved_output > capability.max_output_tokens:
        return OUTPUT_RESERVE_EXCEEDED
    payload = estimate_payload_tokens(capability, messages)
    if payload is None:
        return PAYLOAD_UNCOUNTABLE
    if payload + reserved_output + margin > capability.context_window:
        return CONTEXT_WINDOW_EXCEEDED
    return None


def enforce_generation_budget(
    capability: GenerationCapability | None,
    messages: Sequence[Any],
    reserved_output: int,
    margin: int,
) -> None:
    """核对预算，违反则抛 ``ContextBudgetError``（调用方不得在抛错后再发请求）。"""
    reason = check_generation_payload(capability, messages, reserved_output, margin)
    if reason is None:
        return
    limit = capability.context_window if capability is not None else 0
    payload = estimate_payload_tokens(capability, messages)
    logger.warning(
        "llm_budget_blocked reason=%s model=%s payload=%s reserved=%d margin=%d limit=%d",
        reason,
        capability.model if capability is not None else "-",
        "uncountable" if payload is None else payload,
        reserved_output,
        margin,
        limit,
    )
    raise ContextBudgetError(
        reason,
        payload_tokens=-1 if payload is None else payload,
        reserved_output=reserved_output,
        margin=margin,
        limit=limit,
    )
