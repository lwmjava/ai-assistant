"""切分策略层统一协议与模型。

定义策略接口 ``ChunkingStrategy``、统一切分结果 ``Chunk`` 与策略参数
``ChunkParams``。切分结果带 ``parent_id`` / ``metadata``，为父子文档与溯源打基础。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import wraps
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.rag.embeddings.base import EmbeddingInputPolicy


@dataclass(slots=True)
class Chunk:
    """一个切分结果块。"""

    text: str
    index: int
    parent_id: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class ChunkParams:
    """切分参数，通用字段 + 各策略专用字段（可空）。"""

    chunk_size: int = 500
    chunk_overlap: int = 64
    # 滑动窗口
    window_size: int | None = None
    step: int | None = None
    # token 感知
    max_tokens: int | None = None
    overlap_tokens: int | None = None
    # 语义切分
    similarity_threshold: float | None = None
    # 父子文档
    parent_strategy: str | None = None
    parent_ratio: int = 3
    child_strategy: str | None = None
    child_params: dict | None = None
    # Service-owned provider policy, never a request/child parameter override.
    input_policy: EmbeddingInputPolicy | None = None


class ChunkingStrategy(ABC):
    """切分策略统一接口。"""

    name: str
    _input_policy: Any = None

    def __init_subclass__(cls, **kwargs) -> None:
        """Protect direct subclass calls as well as registry-built strategies.

        The fallback is explicit in metadata; ordinary unstructured text keeps
        its existing strategy behavior. Parent/child owns its composition.
        """
        super().__init_subclass__(**kwargs)
        original = cls.__dict__.get("split")
        if original is not None:

            @wraps(original)
            async def guarded(self, text: str, *, params: ChunkParams) -> list[Chunk]:
                from app.rag.chunking.structure import protected_split, structure_units

                if self.name != "parent_child" and structure_units(text or ""):
                    policy = params.input_policy or getattr(self, "_input_policy", None)
                    if policy is None:
                        policy = getattr(getattr(self, "_embedding", None), "input_policy", None)
                    return protected_split(text, params.chunk_size, policy)
                return await original(self, text, params=params)

            setattr(cls, "split", guarded)
        original_blocks = cls.__dict__.get("split_blocks")
        if original_blocks is not None:

            @wraps(original_blocks)
            async def guarded_blocks(self, blocks: list, *, params: ChunkParams) -> list[Chunk]:
                from app.rag.chunking.structure import structure_units

                ordered = self._order_blocks(blocks) if self.name == "layout_aware" else blocks
                text = "\n".join(str(getattr(block, "text", "")) for block in ordered)
                if structure_units(text):
                    chunks = await self.split(text, params=params)
                    _mark_block_sources(chunks, ordered)
                    return chunks
                return await original_blocks(self, blocks, params=params)

            setattr(cls, "split_blocks", guarded_blocks)

    @abstractmethod
    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        """将文本切分为块列表，空输入返回空列表。"""
        raise NotImplementedError

    async def split_blocks(self, blocks: list, *, params: ChunkParams) -> list[Chunk]:
        """将结构块列表切分为块列表。

        默认回退为拼接块文本后走普通 `split`，供尚未实现结构感知的策略复用。
        """
        from app.rag.chunking.structure import structure_units

        raw_text = "\n".join(str(getattr(block, "text", "")) for block in blocks)
        if structure_units(raw_text):
            chunks = await self.split(raw_text, params=params)
            _mark_block_sources(chunks, blocks)
            return chunks
        text = "\n".join(
            str(getattr(block, "text", "")).strip() for block in blocks if str(getattr(block, "text", "")).strip()
        )
        return await self.split(text, params=params)


def _mark_block_sources(chunks: list[Chunk], blocks: list) -> None:
    """Bind offsets to an explicit parser reading flow, with block provenance."""
    spans = []
    offset = 0
    for index, block in enumerate(blocks):
        text = str(getattr(block, "text", ""))
        spans.append((offset, offset + len(text), index, block))
        offset += len(text) + 1
    for chunk in chunks:
        chunk.metadata["source_coordinate"] = "parsed_reading_flow"
        chunk.metadata["source_join_separator"] = "\n"
        start = chunk.metadata.get("source_start", 0)
        end = chunk.metadata.get("source_end", 0)
        selected = [(index, block) for left, right, index, block in spans if left < end and right > start]
        chunk.metadata["source_block_indices"] = [index for index, _ in selected]
        chunk.metadata["block_provenance"] = [
            {
                "index": index,
                "order": getattr(block, "order", None),
                "page": getattr(block, "page", None),
                "section_path": list(getattr(block, "section_path", []) or []),
            }
            for index, block in selected
        ]
