"""切分策略注册表：按 name 映射策略构造器。"""

from __future__ import annotations

from collections.abc import Callable

from app.rag.chunking.base import ChunkingStrategy

StrategyBuilder = Callable[..., ChunkingStrategy]


class ChunkingRegistry:
    """策略 name -> 构造器 的注册表。"""

    def __init__(self) -> None:
        self._builders: dict[str, StrategyBuilder] = {}

    def register(self, name: str, builder: StrategyBuilder) -> None:
        self._builders[name] = builder

    def names(self) -> list[str]:
        return list(self._builders)

    def build(self, name: str, **kwargs) -> ChunkingStrategy:
        builder = self._builders.get(name)
        if builder is None:
            raise ValueError(f"不支持的切分策略: {name}")
        return builder(**kwargs)
