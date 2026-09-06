"""切分策略分发与 auto 路由。"""

from __future__ import annotations

import re

from app.core.config import settings
from app.rag.chunking.base import ChunkingStrategy
from app.rag.chunking.registry import ChunkingRegistry
from app.rag.chunking.strategies.fixed_chars import FixedCharsChunkingStrategy
from app.rag.chunking.strategies.format_aware import FormatAwareChunkingStrategy
from app.rag.chunking.strategies.layout_aware import LayoutAwareChunkingStrategy
from app.rag.chunking.strategies.paragraph import ParagraphChunkingStrategy
from app.rag.chunking.strategies.parent_child import ParentChildChunkingStrategy
from app.rag.chunking.strategies.recursive import RecursiveChunkingStrategy
from app.rag.chunking.strategies.semantic import SemanticChunkingStrategy
from app.rag.chunking.strategies.sliding_window import SlidingWindowChunkingStrategy
from app.rag.chunking.strategies.structured import StructuredChunkingStrategy
from app.rag.chunking.strategies.token_aware import TokenAwareChunkingStrategy

_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _auto_route(text: str) -> str:
    """按文档特征选择策略。"""
    if _HEADING_RE.search(text):
        return "structured"
    if len(_CJK_RE.findall(text)) / max(1, len(text)) < 0.3:
        return "token_aware"
    if len((text or "").strip()) > settings.RAG_CHUNK_SIZE * 2:
        return "recursive"
    return "paragraph"


def resolve_strategy_name(text: str, strategy: str | None) -> str:
    """解析最终策略名：请求级 > 配置级 > auto 路由。"""
    if strategy and strategy != "auto":
        return strategy
    configured = getattr(settings, "RAG_CHUNK_STRATEGY", "structured")
    if strategy == "auto" or configured == "auto":
        return _auto_route(text)
    return configured


def build_registry() -> ChunkingRegistry:
    """构建默认策略注册表。"""
    registry = ChunkingRegistry()
    registry.register("fixed_chars", lambda **kw: FixedCharsChunkingStrategy())
    registry.register("format_aware", lambda **kw: FormatAwareChunkingStrategy())
    registry.register("layout_aware", lambda **kw: LayoutAwareChunkingStrategy())
    registry.register("paragraph", lambda **kw: ParagraphChunkingStrategy())
    registry.register("recursive", lambda **kw: RecursiveChunkingStrategy())
    registry.register("sliding_window", lambda **kw: SlidingWindowChunkingStrategy())
    registry.register("token_aware", lambda **kw: TokenAwareChunkingStrategy())
    registry.register("structured", lambda **kw: StructuredChunkingStrategy())
    registry.register(
        "semantic", lambda embedding=None, **kw: SemanticChunkingStrategy(embedding)
    )
    registry.register(
        "parent_child",
        lambda embedding=None, **kw: ParentChildChunkingStrategy(
            registry=registry, embedding=embedding
        ),
    )
    return registry


def get_chunking_strategy(name: str, embedding=None) -> ChunkingStrategy:
    """按 name 返回策略实例。"""
    return build_registry().build(name, embedding=embedding)
