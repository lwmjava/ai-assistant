"""检索后处理：剔除注入分块，避免攻击段落占用 top-k。

不做独立 Reranker。只复用 ``PromptInjectionDetector`` 的确定性模式，
把高置信度注入块移出返回列表，再按原融合顺序截断到 ``keep``。
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.rag.vectorstore.base import ChunkResult
from app.security.prompt_injection import PromptInjectionDetector

logger = logging.getLogger(__name__)


def candidate_k(top_k: int) -> int:
    """过取候选数，给注入块剔除留出补位空间。"""
    multiplier = max(1, settings.RAG_RETRIEVAL_CANDIDATE_MULTIPLIER)
    return max(top_k, top_k * multiplier)


def drop_injected_chunks(
    chunks: list[ChunkResult],
    *,
    keep: int,
    detector: PromptInjectionDetector | None = None,
) -> list[ChunkResult]:
    """按原顺序保留未检出注入的分块，最多 ``keep`` 条。"""
    if keep <= 0 or not chunks:
        return []
    if not settings.RAG_DROP_INJECTED_CHUNKS:
        return list(chunks[:keep])

    scanner = detector or PromptInjectionDetector(
        threshold=settings.SECURITY_INJECTION_THRESHOLD
    )
    kept: list[ChunkResult] = []
    for chunk in chunks:
        result = scanner.detect(chunk.content)
        if result.detected:
            logger.info(
                "检索分块因注入模式被剔除: chunk_id=%s document_id=%s matches=%s",
                chunk.id,
                chunk.document_id,
                result.matches,
            )
            continue
        kept.append(chunk)
        if len(kept) >= keep:
            break
    return kept
