"""格式感知切分策略：优先按解析层保留的结构块切分。"""

from __future__ import annotations

from app.rag.chunking.base import Chunk, ChunkingStrategy, ChunkParams
from app.rag.chunking.strategies.recursive import RecursiveChunkingStrategy


class FormatAwareChunkingStrategy(ChunkingStrategy):
    """一个解析块优先对应一个分块，超长块再递归细分。"""

    name = "format_aware"

    def __init__(self) -> None:
        self._recursive = RecursiveChunkingStrategy()

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        """无结构块输入时，退化到递归切分。"""
        return await self._recursive.split(text, params=params)

    async def split_blocks(self, blocks: list, *, params: ChunkParams) -> list[Chunk]:
        chunks: list[Chunk] = []
        for block in blocks:
            text = str(getattr(block, "text", "")).strip()
            if not text:
                continue
            metadata = {
                "block_type": str(getattr(block, "type", "text")),
                "page": getattr(block, "page", None),
                "section_path": list(getattr(block, "section_path", []) or []),
                "source_parser": getattr(block, "metadata", {}).get("parser_name"),
            }
            if len(text) <= params.chunk_size:
                chunks.append(Chunk(text=text, index=len(chunks), metadata=metadata))
                continue
            sub_chunks = await self._recursive.split(text, params=params)
            for sub_chunk in sub_chunks:
                chunks.append(
                    Chunk(
                        text=sub_chunk.text,
                        index=len(chunks),
                        metadata=dict(metadata),
                    )
                )
        return chunks
