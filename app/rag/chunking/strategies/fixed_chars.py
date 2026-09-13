"""固定字符数切分策略：按固定窗口长度切分，可选重叠。"""

from __future__ import annotations

from app.rag.chunking.base import Chunk, ChunkingStrategy, ChunkParams


class FixedCharsChunkingStrategy(ChunkingStrategy):
    """按 ``chunk_size`` 固定窗口切分文本。"""

    name = "fixed_chars"

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        window = max(1, params.chunk_size)
        step = max(1, window - params.chunk_overlap)
        chunks: list[Chunk] = []
        start = 0
        while start < len(text):
            end = min(start + window, len(text))
            piece = text[start:end].strip()
            if piece:
                chunks.append(Chunk(text=piece, index=len(chunks)))
            if end >= len(text):
                break
            start += step
        return chunks
