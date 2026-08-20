"""本地向量库（SQLite + numpy）。

将分块向量与 BM25 词项与应用主库同库存储，零额外基础设施依赖：
- 稠密检索：numpy 批量余弦相似度（向量在写入时已 L2 归一化，余弦即点积）；
- 稀疏检索：经典 BM25；
- 融合：倒数排名融合（RRF）。

该实现面向中小规模知识库，首次检索会将租户全部分块载入内存计算，
数据量大时可平滑替换为 Milvus 等专用向量数据库（接口保持一致）。
"""

import json
import logging
import math

import numpy as np
from sqlmodel import Session, select

from app.models.rag import DocumentChunk
from app.rag.vectorstore.base import ChunkResult, VectorStore

logger = logging.getLogger(__name__)


def _bm25_scores(
    query_terms: list[str],
    doc_tokens: list[list[str]],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """对一批文档计算 BM25 得分。

    Args:
        query_terms: 查询词项。
        doc_tokens: 每个文档归一化后的词项列表。
        k1, b: BM25 超参。

    Returns:
        与各文档一一对应的 BM25 得分（无相关词项为 0.0）。
    """
    n_docs = len(doc_tokens)
    if n_docs == 0:
        return []

    df: dict[str, int] = {}
    for toks in doc_tokens:
        for term in set(toks):
            df[term] = df.get(term, 0) + 1

    # IDF（Robertson / Spark 变体，避免负分）。
    idf = {
        term: math.log((n_docs - freq + 0.5) / (freq + 0.5) + 1.0)
        for term, freq in df.items()
    }
    avgdl = sum(len(t) for t in doc_tokens) / n_docs

    scores: list[float] = []
    for toks in doc_tokens:
        doc_len = len(toks)
        if doc_len == 0:
            scores.append(0.0)
            continue
        freq: dict[str, int] = {}
        for term in toks:
            freq[term] = freq.get(term, 0) + 1
        score = 0.0
        for qt in query_terms:
            f = freq.get(qt)
            if not f:
                continue
            denom = f + k1 * (1.0 - b + b * doc_len / avgdl)
            score += idf.get(qt, 0.0) * (f * (k1 + 1.0)) / denom
        scores.append(score)
    return scores


def _rrf(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """倒数排名融合。

    Args:
        rankings: 多路排序，每路为「文档在候选集里的下标」按相关度降序排成的列表。
        k: RRF 常数，抑制头部过强、拉平多路贡献。

    Returns:
        按融合分降序排成的 [(候选下标, 融合分), ...]。
    """
    fused: dict[int, float] = {}
    for ranking in rankings:
        for position, idx in enumerate(ranking):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + position + 1)
    return sorted(fused.items(), key=lambda x: x[1], reverse=True)


class LocalVectorStore(VectorStore):
    """基于应用主库的本地向量库实现。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    async def add(self, chunks: list) -> None:
        # 分块已随 DocumentChunk 持久化到同一数据库，本地实现无需额外写入。
        return None

    async def delete_by_document(self, document_id: str, tenant_id: str) -> int:
        stmt = select(DocumentChunk).where(
            DocumentChunk.document_id == document_id,
            DocumentChunk.tenant_id == tenant_id,
        )
        rows = self.session.exec(stmt).all()
        for row in rows:
            self.session.delete(row)
        self.session.commit()
        return len(rows)

    async def count(self, tenant_id: str) -> int:
        stmt = select(DocumentChunk).where(DocumentChunk.tenant_id == tenant_id)
        return len(self.session.exec(stmt).all())

    async def hybrid_search(
        self,
        query_embedding: list[float],
        query_tokens: list[str],
        tenant_id: str,
        top_k: int,
        rrf_k: int = 60,
    ) -> list[ChunkResult]:
        stmt = select(DocumentChunk).where(DocumentChunk.tenant_id == tenant_id)
        rows = self.session.exec(stmt).all()
        if not rows:
            return []

        embeddings: list[list[float]] = []
        tokens: list[list[str]] = []
        valid: list[DocumentChunk] = []
        for row in rows:
            if not row.embedding:
                continue
            try:
                emb = json.loads(row.embedding)
                toks = json.loads(row.tokens) if row.tokens else []
            except json.JSONDecodeError:
                continue
            embeddings.append(emb)
            tokens.append(toks)
            valid.append(row)

        if not valid:
            return []

        # ── 稠密检索：余弦（向量已归一化，点积即得余弦）──
        matrix = np.array(embeddings, dtype=np.float64)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix = matrix / norms
        q = np.array(query_embedding, dtype=np.float64)
        q_norm = np.linalg.norm(q)
        if q_norm > 0:
            q = q / q_norm
        dense = matrix @ q
        dense_order = np.argsort(-dense).tolist()

        # ── 稀疏检索：BM25 ──
        bm25 = _bm25_scores(query_tokens, tokens)
        # BM25 可能为全 0（查询词项均未见），此时稀疏排序退化为原序。
        if any(s > 0 for s in bm25):
            sparse_order = list(np.argsort(-np.array(bm25)).tolist())
        else:
            sparse_order = list(range(len(valid)))

        # ── RRF 融合 ──
        fused = _rrf([dense_order, sparse_order], k=rrf_k)
        results: list[ChunkResult] = []
        for idx, score in fused[:top_k]:
            row = valid[idx]
            results.append(
                ChunkResult(
                    id=row.id,
                    content=row.content,
                    source=row.source,
                    document_id=row.document_id,
                    score=float(score),
                )
            )
        return results
