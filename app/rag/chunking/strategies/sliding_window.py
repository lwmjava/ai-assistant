"""滑动窗口切分策略：固定窗口 + 步长，不依赖句子边界。"""

from __future__ import annotations

from app.rag.chunking.base import Chunk, ChunkingStrategy, ChunkParams


class SlidingWindowChunkingStrategy(ChunkingStrategy):
    """按 ``window_size`` 窗口、``step`` 步长滑动切分。"""

    name = "sliding_window"

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        window = params.window_size or params.chunk_size
        step = params.step or max(1, window - params.chunk_overlap)
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
