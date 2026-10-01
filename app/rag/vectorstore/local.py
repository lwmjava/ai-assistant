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
from datetime import UTC, datetime

import numpy as np
from sqlmodel import Session, select

from app.core.config import settings
from app.models.rag import Document, DocumentChunk
from app.rag.effective_date import document_version_status, ensure_utc
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


def _bm25_all_zero_reason(
    query_tokens: list[str], doc_tokens: list[list[str]]
) -> tuple[str, int]:
    """判定 BM25 全 0 的单一原因码，并统计词项为空的候选数。

    判定顺序：empty_query_tokens → empty_doc_tokens → no_overlap。
    """
    empty_doc_count = sum(1 for toks in doc_tokens if not toks)
    if not query_tokens:
        return "empty_query_tokens", empty_doc_count
    if empty_doc_count == len(doc_tokens) and len(doc_tokens) > 0:
        return "empty_doc_tokens", empty_doc_count
    return "no_overlap", empty_doc_count


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
        as_of: datetime | None = None,
        schedule_at: datetime | None = None,
    ) -> list[ChunkResult]:
        stmt = (
            select(DocumentChunk)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(DocumentChunk.tenant_id == tenant_id)
        )
        stmt = stmt.where(Document.deleted_at.is_(None))
        if not settings.RAG_EFFECTIVE_DATE_FILTER:
            stmt = stmt.where(Document.is_current.is_(True))
        rows = self.session.exec(stmt).all()
        if not rows:
            return []
        rows, version_by_chunk = visible_chunks_with_status(
            self.session, rows, as_of, schedule_at
        )
        if not rows:
            return []

        expected_dim = len(query_embedding)
        embeddings: list[list[float]] = []
        tokens: list[list[str]] = []
        valid: list[DocumentChunk] = []
        skipped_dim = 0
        for row in rows:
            if not row.embedding:
                continue
            try:
                emb = json.loads(row.embedding)
                toks = json.loads(row.tokens) if row.tokens else []
            except json.JSONDecodeError:
                continue
            if not isinstance(emb, list) or len(emb) != expected_dim:
                skipped_dim += 1
                continue
            embeddings.append(emb)
            tokens.append(toks)
            valid.append(row)

        if skipped_dim:
            logger.warning(
                "跳过与查询向量维度不一致的分块: skipped=%s expected_dim=%s tenant=%s",
                skipped_dim,
                expected_dim,
                tenant_id,
            )
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
        # 全 0 时稀疏路不进 RRF，避免载入顺序扰动稠密排序。
        if any(s > 0 for s in bm25):
            sparse_order = list(np.argsort(-np.array(bm25)).tolist())
        else:
            sparse_order = []
            reason, empty_doc_count = _bm25_all_zero_reason(query_tokens, tokens)
            logger.info(
                "bm25_all_zero tenant_id=%s candidate_count=%s "
                "query_token_count=%s empty_doc_count=%s reason=%s",
                tenant_id,
                len(valid),
                len(query_tokens),
                empty_doc_count,
                reason,
            )

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
                    version_status=version_by_chunk.get(row.id, "current"),
                )
            )
        return results


def visible_chunks_with_status(
    session: Session,
    rows: list[DocumentChunk],
    as_of: datetime | None,
    schedule_at: datetime | None,
) -> tuple[list[DocumentChunk], dict[str, str]]:
    if not settings.RAG_EFFECTIVE_DATE_FILTER:
        return rows, {row.id: "current" for row in rows}
    moment = ensure_utc(as_of) if as_of is not None else datetime.now(UTC)
    visible: list[DocumentChunk] = []
    version_by_chunk: dict[str, str] = {}
    doc_cache: dict[str, Document | None] = {}
    for row in rows:
        if row.document_id not in doc_cache:
            doc_cache[row.document_id] = session.get(Document, row.document_id)
        doc = doc_cache[row.document_id]
        if doc is None:
            continue
        status = document_version_status(
            is_current=doc.is_current,
            effective_at=doc.effective_at,
            expires_at=doc.expires_at,
            as_of=moment,
            schedule_at=schedule_at,
        )
        if status is None:
            continue
        visible.append(row)
        version_by_chunk[row.id] = status
    return visible, version_by_chunk
