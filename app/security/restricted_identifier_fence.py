"""生成输出围栏：受限标识抑制（确定性，零额外 LLM 调用）。

RAG-043：RAG-035 真实评测发现，检索上下文含受限文档时模型会披露受限标识
（rag-036 披露受限工号 NW-HR-001）。本模块在**输出侧**用确定性正则检测并就地
屏蔽受限标识，使其不再到达用户、也不再被 RAG-035 零容忍规则判为泄露。

设计约束：
- 纯函数、无网络、无 LLM 调用；
- 注册表可注入，内置种子含已知受限标识；
- 独立 ``enabled`` 开关，关闭时原样返回（可回滚）；
- 边界锚定，避免误伤同形态合法 ID（如打印机型号 NW-PRINT-X1）；
- fail-open：匹配异常不污染原文（由调用方决定告警策略）。

注意：资源级检索 ACL 仍为 Planned（RAG-041）。本围栏是输出侧**兜底**，不是检索侧
权限过滤；二者互补，不互相替代。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RestrictedIdentifier:
    """一条受限标识注册表项。"""

    id: str  # 注册表键（人类可读，如 "NW-HR-001"）
    pattern: re.Pattern[str]  # 已编译匹配规则


def builtin_restricted_registry() -> list[RestrictedIdentifier]:
    """内置受限标识注册表 v0.1。

    种子 NW-HR-001：员工手册内部编制编号（语料 kb-hr-private.md）。
    大小写不敏感；分隔符容忍 ``- _ 空白``；前后词边界锚定，避免误伤合法相似 ID
    或更长数字串（如 NW-HR-0010）。
    """
    return [
        RestrictedIdentifier(
            id="NW-HR-001",
            pattern=re.compile(r"\bnw[\s_\-]?hr[\s_\-]?001\b", re.IGNORECASE),
        ),
    ]


@dataclass
class FenceResult:
    """围栏判定结果。"""

    flagged: bool = False
    hits: list[str] = field(default_factory=list)
    redacted_text: str = ""


class RestrictedIdentifierFence:
    """受限标识输出围栏。

    Usage::

        fence = RestrictedIdentifierFence()
        out = fence.filter(model_output)
        if out.flagged:
            model_output = out.redacted_text
    """

    def __init__(
        self,
        registry: list[RestrictedIdentifier] | None = None,
        *,
        enabled: bool = True,
        replacement: str = "[受限内容已屏蔽]",
    ) -> None:
        self._registry = registry if registry is not None else builtin_restricted_registry()
        self._enabled = enabled
        self._replacement = replacement

    def filter(self, text: str) -> FenceResult:
        """检测并屏蔽文本中的受限标识。未开启或无命中时原样返回。"""
        if not self._enabled or not text:
            return FenceResult(flagged=False, redacted_text=text or "")

        redacted = text
        hits: list[str] = []
        for entry in self._registry:
            try:
                new_text, n = entry.pattern.subn(self._replacement, redacted)
            except re.error:
                # fail-open：单条注册表异常不污染原文、不阻断整条链。
                continue
            if n:
                hits.append(entry.id)
                redacted = new_text

        return FenceResult(
            flagged=bool(hits),
            hits=hits,
            redacted_text=redacted,
        )
