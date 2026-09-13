"""递归切分策略：优先按自然边界切分，必要时回退到字符级硬切。"""

from __future__ import annotations

import re

from app.rag.chunking.base import Chunk, ChunkingStrategy, ChunkParams
from app.rag.chunking.strategies.fixed_chars import FixedCharsChunkingStrategy

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[，；,;])")


def _split_on_double_newline(text: str) -> list[str]:
    return [part.strip() for part in text.split("\n\n") if part.strip()]


def _split_on_newline(text: str) -> list[str]:
    return [part.strip() for part in text.splitlines() if part.strip()]


def _split_on_sentence(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]


def _split_on_clause(text: str) -> list[str]:
    return [part.strip() for part in _CLAUSE_SPLIT_RE.split(text) if part.strip()]


class RecursiveChunkingStrategy(ChunkingStrategy):
    """按空行、换行、句子、短句逐级递归切分。"""

    name = "recursive"

    def __init__(self) -> None:
        self._fixed = FixedCharsChunkingStrategy()
        self._splitters = (
            _split_on_double_newline,
            _split_on_newline,
            _split_on_sentence,
            _split_on_clause,
        )

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        pieces = await self._split_recursive(text, params=params, level=0)
        return [Chunk(text=piece, index=i) for i, piece in enumerate(pieces) if piece]

    async def _split_recursive(
        self, text: str, *, params: ChunkParams, level: int
    ) -> list[str]:
        text = text.strip()
        if not text:
            return []
        if len(text) <= params.chunk_size:
            return [text]
        if level >= len(self._splitters):
            chunks = await self._fixed.split(text, params=params)
            return [chunk.text for chunk in chunks]

        parts = self._splitters[level](text)
        if len(parts) <= 1:
            return await self._split_recursive(text, params=params, level=level + 1)

        result: list[str] = []
        buffer = ""
        for part in parts:
            candidate = f"{buffer}{part}" if buffer else part
            if len(candidate) <= params.chunk_size:
                buffer = candidate
                continue
            if buffer:
                result.extend(
                    await self._split_recursive(buffer, params=params, level=level + 1)
                )
                buffer = ""
            if len(part) <= params.chunk_size:
                buffer = part
            else:
                result.extend(
                    await self._split_recursive(part, params=params, level=level + 1)
                )
        if buffer:
            result.extend(
                await self._split_recursive(buffer, params=params, level=level + 1)
            )
        return result
