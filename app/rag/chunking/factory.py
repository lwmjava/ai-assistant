"""切分策略分发与 auto 路由。"""

from __future__ import annotations

from app.core.config import settings
from app.rag.chunking.base import ChunkingStrategy
from app.rag.chunking.registry import ChunkingRegistry
from app.rag.chunking.routing import (
    RoutingDecision,
    route,
    signals_from_parsed,
    signals_from_text,
)
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


def _long_threshold() -> int:
    return max(1, settings.RAG_CHUNK_SIZE * 2)


def resolve_strategy_name(text: str, strategy: str | None) -> str:
    """解析最终策略名：请求级 > 配置级 > auto 路由。向后兼容旧签名。"""
    return resolve_strategy_with_decision(text, strategy).strategy


def resolve_strategy_with_decision(
    text: str,
    strategy: str | None,
    *,
    parsed=None,
    configured_default: str | None = None,
) -> RoutingDecision:
    """返回策略名与可解释决策；parsed 存在时聚合 blocks/bbox 信号。

    优先级：显式请求（非 auto）> 配置级或请求级 auto 路由 > 配置默认。
    """
    configured: str = configured_default or str(getattr(settings, "RAG_CHUNK_STRATEGY", "structured"))
    signals = signals_from_parsed(parsed) if parsed is not None else signals_from_text(text)

    # 显式指定的非 auto 策略直接命中。
    if strategy and strategy != "auto":
        return RoutingDecision(
            strategy=strategy,
            reason="request_override",
            signals=signals,
        )
    # 配置为 auto 或请求为 auto 时跑路由决策表。
    if configured == "auto" or strategy == "auto":
        return route(
            signals,
            requested=strategy,
            configured_default=configured,
            long_threshold_chars=_long_threshold(),
        )
    # 否则使用配置默认策略。
    return RoutingDecision(
        strategy=configured,
        reason="configured_default",
        signals=signals,
    )


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
