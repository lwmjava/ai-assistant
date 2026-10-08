"""RAG-031 LLM 辅助切分边界。

设计约束（ADR-0005 §3/§9 + RAG-031 契约）：

- 模型**只输出边界 ID**（句子序号数组），**绝不输出或改写原文**；原文由代码按序号
  映射到字符偏移后切取。
- 输出严格校验：坏 JSON / 非整数 / 越界 / 注入式 prose / 超时 / 预算耗尽，一律
  返回 ``degraded``，由调用方回退既有规则切分。
- 有限调用：单文档调用次数有上限（费用护栏）；真正发请求走 RAG-028 的
  ``LLMProvider.chat``（其内已做上下文预算 Guard）。
- 默认关闭：只有服务端在配置开启时才构造 ``BoundaryAdvisor`` 并注入切分参数。

本模块不直接访问数据库，不持有密钥；元数据只记录模型名与版本号，不记录正文。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider

logger = logging.getLogger(__name__)

PROMPT_VERSION = "llm-boundary-prompt-v0.1"
# 协议：输出为「句子序号数组」，序号从 1 起；代码负责把序号映射成字符偏移。
PROTOCOL_VERSION = "llm-boundary-protocol-v0.1"

_SENTENCE_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")
_FENCE_OPEN = re.compile(r"^```[a-zA-Z]*\n?")
_FENCE_CLOSE = re.compile(r"\n?```$")

# 降级原因码（稳定字符串，进 chunk metadata 与评测报告）。
DEGRADE_TOO_FEW_UNITS = "too_few_sentences"
DEGRADE_TOO_LONG = "region_exceeds_input_budget"
DEGRADE_BUDGET = "call_budget_exhausted"
DEGRADE_TIMEOUT = "provider_timeout"
DEGRADE_ERROR = "provider_error"
DEGRADE_BAD_JSON = "bad_json_or_non_integer"

_SYSTEM_PROMPT = (
    "你是文档切分边界助手。你只输出一个 JSON 整数数组，"
    "表示建议在哪些句子之后切分主题边界。"
    "规则：数组元素是 1 到 N 之间的句子序号（N 为句子总数），升序、去重；"
    "如果没有合适的主题边界，输出空数组 []。"
    "不要输出任何解释、原文改写、Markdown 代码块或额外文字。"
)


@dataclass(frozen=True)
class BoundaryOutcome:
    """一次边界建议的结果。``offsets`` 相对传入 region 的字符偏移。"""

    kind: str  # "offsets" | "degraded"
    offsets: tuple[int, ...] = ()
    reason: str = ""
    calls_used: int = 0


def split_sentence_spans(region: str) -> list[tuple[int, int]]:
    """把正文切成句子级 (start, end) 区间，区间为半开、按原文顺序。"""
    text = region or ""
    spans: list[tuple[int, int]] = []
    for match in _SENTENCE_RE.finditer(text):
        if match.end() > match.start():
            spans.append((match.start(), match.end()))
    return spans


def parse_boundary_indices(raw: str, n: int) -> list[int] | None:
    """严格解析模型输出为合法句子序号列表。

    合法返回升序去重列表（可能为空）；任何异常（含注入式 prose、非整数、越界）
    返回 ``None``，由调用方规则降级。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = _FENCE_OPEN.sub("", text)
        text = _FENCE_CLOSE.sub("", text).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    seen: set[int] = set()
    for item in data:
        # bool 是 int 的子类，必须先排除；序号必须在 [1, n-1]。
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        if item < 1 or item > n - 1:
            return None
        seen.add(item)
    return sorted(seen)


class BoundaryAdvisor:
    """对单个文档有界的边界建议器。每个文档应新建一个实例（独立计数器）。"""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_calls: int,
        max_chars_per_call: int,
        output_tokens: int,
        timeout: float,
    ) -> None:
        self._provider = provider
        self._max_calls = max(1, int(max_calls))
        self._max_chars = max(1, int(max_chars_per_call))
        self._output_tokens = max(1, int(output_tokens))
        self._timeout = float(timeout)
        self._calls = 0

    @property
    def calls_used(self) -> int:
        return self._calls

    def metadata_snapshot(self) -> dict[str, object]:
        """不含正文/密钥的可记录描述。"""
        return {
            "llm_boundary_model": getattr(self._provider, "model", "unknown"),
            "llm_boundary_prompt_version": PROMPT_VERSION,
            "llm_boundary_protocol_version": PROTOCOL_VERSION,
            "llm_boundary_max_calls": self._max_calls,
        }

    def _build_messages(self, region: str, spans: list[tuple[int, int]]) -> list[ChatMessage]:
        numbered = "\n".join(f"{i}. {region[s:e]}" for i, (s, e) in enumerate(spans, start=1))
        user = f"句子总数 N={len(spans)}。\n{numbered}\n请输出主题边界序号数组。"
        return [
            ChatMessage(role=ChatRole.SYSTEM, content=_SYSTEM_PROMPT),
            ChatMessage(role=ChatRole.USER, content=user),
        ]

    async def suggest(self, region: str, *, target_chars: int) -> BoundaryOutcome:
        """对一段正文间隙建议主题边界偏移；任何失败都降级，不抛给切分管线。"""
        text = region or ""
        used = self._calls
        if self._calls >= self._max_calls:
            return BoundaryOutcome(kind="degraded", reason=DEGRADE_BUDGET, calls_used=used)
        if len(text) > self._max_chars:
            return BoundaryOutcome(kind="degraded", reason=DEGRADE_TOO_LONG, calls_used=used)
        spans = split_sentence_spans(text)
        if len(spans) < 2:
            return BoundaryOutcome(kind="degraded", reason=DEGRADE_TOO_FEW_UNITS, calls_used=used)

        messages = self._build_messages(text, spans)
        options = LLMOptions(
            temperature=0.0,
            max_tokens=self._output_tokens,
            timeout=self._timeout,
        )
        self._calls += 1
        try:
            raw = await self._provider.chat(messages, options)
        except TimeoutError:
            logger.warning("llm_boundary_timeout")
            return BoundaryOutcome(kind="degraded", reason=DEGRADE_TIMEOUT, calls_used=self._calls)
        except Exception:  # noqa: BLE001 — RAG-028 预算 Guard / 网络 / 解析错误一律降级
            logger.warning("llm_boundary_provider_error")
            return BoundaryOutcome(kind="degraded", reason=DEGRADE_ERROR, calls_used=self._calls)

        indices = parse_boundary_indices(raw, len(spans))
        if indices is None:
            return BoundaryOutcome(kind="degraded", reason=DEGRADE_BAD_JSON, calls_used=self._calls)
        offsets = tuple(spans[i - 1][1] for i in indices)
        return BoundaryOutcome(kind="offsets", offsets=offsets, calls_used=self._calls)
