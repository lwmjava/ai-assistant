"""父子文档切分策略：父块为子块的倍数粗切分块，子块为细粒度块。"""

from __future__ import annotations

from app.rag.chunking.base import Chunk, ChunkingStrategy, ChunkParams
from app.rag.chunking.registry import ChunkingRegistry


class ParentChildChunkingStrategy(ChunkingStrategy):
    """父块 = 子块 N 倍粗块；子块记录 ``parent_id`` 指向父块。"""

    name = "parent_child"

    def __init__(self, *, registry: ChunkingRegistry | None, embedding=None) -> None:
        self._registry = registry
        self._embedding = embedding

    def _build_child(self, name: str) -> ChunkingStrategy:
        if self._registry is not None:
            return self._registry.build(name, embedding=self._embedding)
        # 回退到 factory，避免顶层循环导入。
        from app.rag.chunking.factory import get_chunking_strategy

        return get_chunking_strategy(name, embedding=self._embedding)

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        child_name = params.child_strategy or "paragraph"
        parent_name = params.parent_strategy or "paragraph"
        ratio = params.parent_ratio or 3

        child_strategy = self._build_child(child_name)
        child_merged = {
            "chunk_size": params.chunk_size,
            "chunk_overlap": params.chunk_overlap,
        }
        child_merged.update(params.child_params or {})
        child_params = ChunkParams(**child_merged)
        children = await child_strategy.split(text, params=child_params)

        parent_size = max(1, params.chunk_size * ratio)
        parent_strategy = self._build_child(parent_name)
        parent_params = ChunkParams(
            chunk_size=parent_size,
            chunk_overlap=params.chunk_overlap,
        )
        parents = await parent_strategy.split(text, params=parent_params)

        result: list[Chunk] = []
        for i, parent in enumerate(parents):
            parent.metadata["kind"] = "parent"
            parent.metadata["parent_key"] = f"p{i}"
            result.append(parent)
        per_parent = max(1, len(children) // max(1, len(parents)))
        for j, child in enumerate(children):
            parent_idx = min(j // per_parent, len(parents) - 1)
            child.parent_id = f"p{parent_idx}"
            child.metadata["kind"] = "child"
            result.append(child)
        return result
