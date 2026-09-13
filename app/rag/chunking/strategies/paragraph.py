"""段落切分策略：按空行 / 段落边界切分。"""

from __future__ import annotations

from app.rag.chunking.base import Chunk, ChunkingStrategy, ChunkParams


def _hard_split(text: str, chunk_size: int) -> list[str]:
    """段落内超长时按字符硬切。"""
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


class ParagraphChunkingStrategy(ChunkingStrategy):
    """按空行分段，超长段落内部按字符硬切。"""

    name = "paragraph"

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: list[Chunk] = []
        for para in paragraphs:
            pieces = (
                [para]
                if len(para) <= params.chunk_size
                else _hard_split(para, params.chunk_size)
            )
            for piece in pieces:
                chunks.append(Chunk(text=piece, index=len(chunks)))
        return chunks
