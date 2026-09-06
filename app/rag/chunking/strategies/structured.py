"""结构切分策略：按 Markdown 标题分节，复用现有句子切分逻辑。"""

from __future__ import annotations

from app.rag.chunking.base import Chunk, ChunkingStrategy, ChunkParams
from app.rag.ingestion import split_text_structured


class StructuredChunkingStrategy(ChunkingStrategy):
    """复用现有 ``split_text_structured``，将其包装为统一 Chunk 输出。"""

    name = "structured"

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        pieces = split_text_structured(text, params.chunk_size, params.chunk_overlap)
        return [Chunk(text=piece, index=i) for i, piece in enumerate(pieces)]
