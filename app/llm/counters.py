"""模型调用的 token 计数。

计数只有两条路，且必须把用的是哪条写进能力契约：

1. **官方计数器优先**。能拿到厂商官方 tokenizer 就用它；
2. **没有官方计数器时用经校准的保守估算**，并明确局限。

局限（必须如实披露，不得让读数字的人以为它是精确值）：

- ``utf8_byte_count`` 是**估算**，不是精确 tokenizer 输出。一个汉字 3 字节 ≥ 1 token，
  英文约 4 字符 1 token，因此「UTF-8 字节数」一般 ≥ token 数。把它当 token 上界
  使用是**偏保守**的：宁可把刚好够用的请求拦下，也不放行超限请求。
- 计不出来的时候**不计 0**。多模态内容序列化失败、正文含不可编码字符都会抛
  ``PayloadUncountableError``，由护栏按「无法计数」拒绝发送。
- 本机 2026-10-07 实测未安装 ``tiktoken``，当时退回保守估算；这是历史环境记录。
  ``tiktoken`` 是否可用以实际环境为准。**安装后** ``official_counter_for``
  会自动返回真实计数器并被优先使用，无需改代码、也无需改能力表。
"""

from __future__ import annotations

import json
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


class PayloadUncountableError(RuntimeError):
    """内容无法可靠计数。

    计不出来就是「不知道会不会超限」——按本卡的立场只能拒绝发送，
    不能按 0 放行：计 0 等于宣布任何窗口都装得下。
    """


def message_content(message: Any) -> str:
    """取出一条消息计进窗口的正文。

    字符串原样返回；非字符串（OpenAI 多模态的 list/dict content，如
    ``image_url``）按 JSON 序列化后的文本计——按 Python ``repr`` 计会把一张图
    算成几十字节，方向是彻底的低估。序列化不了就抛 ``PayloadUncountableError``。
    """
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise PayloadUncountableError("多模态内容无法序列化，无法计数") from exc


def utf8_byte_count(text: str) -> int:
    """保守估算：UTF-8 字节数。

    字节数一般 ≥ token 数，因此偏保守；**不是**精确 tokenizer 输出。
    不可编码字符（如孤立代理项）会让「字节数 ≥ token 数」这条依据失效，
    因此直接抛 ``PayloadUncountableError``，不静默计 0。
    """
    try:
        return len((text or "").encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise PayloadUncountableError("正文含无法编码的字符，无法计数") from exc


def estimate_messages_tokens(messages: Sequence[Any]) -> int:
    """估算一组消息占用的 token 数：正文字节数 + 每消息框架开销 + 回复引导。

    任何一条正文计不出来就抛 ``PayloadUncountableError``（fail-closed）。
    """
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
            content = message_content(message)
            # tiktoken会替换孤立代理项，先验证正文能按原内容编码，维持fail-closed。
            utf8_byte_count(content)
            total += _MESSAGE_OVERHEAD + len(encoding.encode(content))
        return total

    return count
