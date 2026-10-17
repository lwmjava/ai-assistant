"""RAG-022 自动切分策略路由决策表。

把原 ``factory._auto_route(text)`` 的启发式升级为可追溯、可解释的决策表，
输入扩展为文档类型（extension/content_type）、解析块（blocks）、bbox、
Markdown 标题、围栏/盒图/表格结构单元、CJK 比例与长度。

设计约束（ADR-0005 + RAG-022 non-goals）：
- 不强行覆盖十种策略；semantic / parent_child 仍为显式 opt-in。
- 缺结构信号时显式保守降级，不伪称 layout/format。
- 决策 reason 非空，写入 chunk metadata 以支持审计与重放。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.chunking.structure import structure_units

ROUTING_VERSION = "auto-routing-v0.1"

_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


@dataclass(frozen=True)
class RoutingSignals:
    """路由输入信号；全部为可序列化原始事实，不含正文。"""

    extension: str | None = None
    content_type: str | None = None
    has_blocks: bool = False
    has_bbox: bool = False
    is_text_parser: bool = False
    block_types: tuple[str, ...] = field(default_factory=tuple)
    has_headings: bool = False
    has_structure_units: bool = False
    cjk_ratio: float = 0.0
    char_count: int = 0


@dataclass(frozen=True)
class RoutingDecision:
    """路由结果；strategy 为最终策略名，reason 可解释。"""

    strategy: str
    reason: str
    signals: RoutingSignals
    version: str = ROUTING_VERSION


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    return len(_CJK_RE.findall(text)) / len(text)


def signals_from_text(text: str) -> RoutingSignals:
    """纯文本路径（ingest_text / 旧兼容调用）。"""
    body = text or ""
    return RoutingSignals(
        extension=None,
        content_type=None,
        has_blocks=False,
        has_bbox=False,
        block_types=(),
        has_headings=bool(_HEADING_RE.search(body)),
        has_structure_units=bool(structure_units(body)),
        cjk_ratio=_cjk_ratio(body),
        char_count=len(body),
    )


def signals_from_parsed(parsed) -> RoutingSignals:
    """解析路径（ingest_parsed_document）：聚合 blocks / bbox / 元数据。"""
    text = parsed.text or ""
    blocks = list(parsed.blocks or [])
    block_types = tuple(sorted({str(getattr(b, "type", "")) for b in blocks if getattr(b, "type", None)}))
    has_bbox = any(getattr(b, "bbox", None) is not None for b in blocks)
    return RoutingSignals(
        extension=getattr(parsed, "extension", None),
        content_type=getattr(parsed, "content_type", None),
        has_blocks=bool(blocks),
        has_bbox=has_bbox,
        is_text_parser=(parsed.metadata or {}).get("parser_name") == "text",
        block_types=block_types,
        has_headings=bool(_HEADING_RE.search(text)),
        has_structure_units=bool(structure_units(text)),
        cjk_ratio=_cjk_ratio(text),
        char_count=len(text),
    )


def route(
    signals: RoutingSignals,
    *,
    requested: str | None,
    configured_default: str,
    long_threshold_chars: int | None = None,
) -> RoutingDecision:
    """按决策表返回策略；显式请求优先，auto/缺省走信号。"""
    if long_threshold_chars is None:
        try:
            from app.core.config import settings

            long_threshold_chars = max(1, settings.RAG_CHUNK_SIZE * 2)
        except Exception:  # noqa: BLE001 — 测试/离线环境回退
            long_threshold_chars = 1000
    if requested and requested != "auto":
        return RoutingDecision(
            strategy=requested,
            reason="request_override",
            signals=signals,
        )

    # TextDocumentParser 的行块 strip 过空白，不能替代保真的正文。
    # 1) bbox 多栏/版式块 -> layout_aware
    if signals.has_bbox and not signals.is_text_parser:
        return RoutingDecision(
            strategy="layout_aware",
            reason="bbox_layout_blocks",
            signals=signals,
        )
    # 2) 有结构块但无 bbox -> format_aware
    if signals.has_blocks and not signals.is_text_parser:
        return RoutingDecision(
            strategy="format_aware",
            reason="structural_blocks",
            signals=signals,
        )
    # 3) Markdown 标题
    if signals.has_headings:
        return RoutingDecision(
            strategy="structured",
            reason="markdown_headings",
            signals=signals,
        )
    # 4) 围栏/盒图/表格等结构单元（RAG-021 负责完整保护）
    if signals.has_structure_units:
        return RoutingDecision(
            strategy="structured",
            reason="structure_units",
            signals=signals,
        )
    # 5) 非中文/代码主导
    if signals.cjk_ratio < 0.3:
        return RoutingDecision(
            strategy="token_aware",
            reason="non_cjk_or_code",
            signals=signals,
        )
    # 6) 长纯文本
    if signals.char_count > long_threshold_chars:
        return RoutingDecision(
            strategy="recursive",
            reason="long_plain_text",
            signals=signals,
        )
    # 7) 兜底短纯文本
    return RoutingDecision(
        strategy="paragraph",
        reason="short_plain_text",
        signals=signals,
    )
