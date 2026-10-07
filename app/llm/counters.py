"""模型调用的 token 计数。

计数只有两条路，且必须把用的是哪条写进能力契约：

1. **官方计数器优先**。能拿到厂商官方 tokenizer 就用它；
2. **没有官方计数器时用经校准的保守估算**，并明确局限。

局限（必须如实披露，不得让读数字的人以为它是精确值）：

- ``utf8_byte_count`` 是**估算**，不是精确 tokenizer 输出。一个汉字 3 字节 ≥ 1 token，
  英文约 4 字符 1 token，因此「UTF-8 字节数」一般 ≥ token 数。把它当 token 上界
  使用是**偏保守**的：宁可把刚好够用的请求拦下，也不放行超限请求。
- 官方计数器 ``tiktoken`` 未随本仓库安装（本机 2026-10-07 实测 ``No module named
  'tiktoken'``），因此当前一律退回保守估算。**安装后** ``official_counter_for``
  会自动返回真实计数器并被优先使用，无需改代码、也无需改能力表。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

# 保守估算的方法名。能力契约、日志与测试都按它解读数字口径。
UTF8_METHOD = "utf8-bytes-conservative-estimate"
# 官方计数器的方法名。只有计数器真正可用时才会出现。
OFFICIAL_METHOD = "official-tokenizer"

# 每条消息的框架开销（role 标记、内容起止分隔符）与末尾的回复引导。
# 口径取自 OpenAI 官方 cookbook：每条消息 3 token 框架开销 + 回复引导 3 token；
# 这里每条消息按 4 计（3 个框架 + 1 个留给 name 等字段），只算正文会系统性偏低，
# 消息条数多时偏差会被放大——工具调用与自纠错轮次恰好都是「条数多」的场景。
_MESSAGE_OVERHEAD = 4
_REPLY_PRIMER = 3


def message_content(message: Any) -> str:
    """取出一条消息计进窗口的正文。缺失内容按空串计，不抛异常。"""
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def utf8_byte_count(text: str) -> int:
    """保守估算：UTF-8 字节数。

    字节数一般 ≥ token 数，因此偏保守；**不是**精确 tokenizer 输出。
    不可编码字符不计数，避免计数本身把请求打挂。
    """
    return len((text or "").encode("utf-8", errors="ignore"))


def estimate_messages_tokens(messages: Sequence[Any]) -> int:
    """估算一组消息占用的 token 数：正文字节数 + 每消息框架开销 + 回复引导。"""
    total = _REPLY_PRIMER
    for message in messages:
        total += _MESSAGE_OVERHEAD + utf8_byte_count(message_content(message))
    return total


def official_counter_for(deployment: str, model: str) -> Callable[[Sequence[Any]], int] | None:
    """返回该 deployment / model 的官方计数器，不可用时返回 None。

    只有 OpenAI 官方域名才认 ``tiktoken``：同一份代码也会打到自建网关和别的厂商，
    拿 OpenAI 的 tokenizer 去数别家的 token 是伪造精度。计数器未安装或模型未登记
    时返回 None，由调用方退回保守估算。
    """
    if not (deployment or "").startswith("api.openai.com"):
        return None
    try:
        import tiktoken  # 惰性导入：未安装也不能让能力解析失败
    except ImportError:
        return None
    try:
        encoding = tiktoken.encoding_for_model(model)
    except Exception:  # noqa: BLE001 — 模型未登记时同样退回估算
        return None

    def count(messages: Sequence[Any]) -> int:
        total = _REPLY_PRIMER
        for message in messages:
            total += _MESSAGE_OVERHEAD + len(encoding.encode(message_content(message)))
        return total

    return count
