"""语义切分策略：用 embedding 计算相邻候选相似度，低于阈值处断开。"""

from __future__ import annotations

import re

from app.rag.chunking.base import Chunk, ChunkingStrategy, ChunkParams

_SENTENCE_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


class SemanticChunkingStrategy(ChunkingStrategy):
    """复用 embedding provider，按相邻句子相似度切分。"""

    name = "semantic"

    def __init__(self, embedding) -> None:
        if embedding is None:
            raise ValueError("语义切分需要 embedding provider")
        self._embedding = embedding

    async def split(self, text: str, *, params: ChunkParams) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]
        if len(sentences) <= 1:
            return [Chunk(text=text, index=0)]
        vectors = await self._embedding.embed(sentences)
        threshold = params.similarity_threshold or 0.5
        chunks: list[Chunk] = []
        buf = sentences[0]
        prev = vectors[0]
        for sentence, vec in zip(sentences[1:], vectors[1:]):
            if _cosine(prev, vec) < threshold:
                chunks.append(Chunk(text=buf, index=len(chunks)))
                buf = sentence
            else:
                buf += sentence
            prev = vec
        if buf.strip():
            chunks.append(Chunk(text=buf, index=len(chunks)))
        return chunks
