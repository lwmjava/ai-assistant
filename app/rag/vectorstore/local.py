"""本地向量库（SQLite + numpy）。

将分块向量与 BM25 词项与应用主库同库存储，零额外基础设施依赖：
- 稠密检索：numpy 批量余弦相似度（向量在写入时已 L2 归一化，余弦即点积）；
- 稀疏检索：经典 BM25；
- 融合：倒数排名融合（RRF）。

该实现面向中小规模知识库，首次检索会将租户全部分块载入内存计算，
数据量大时可平滑替换为 Milvus 等专用向量数据库（接口保持一致）。
"""

import asyncio
import json
import logging
import math
from collections.abc import Sequence
from datetime import UTC, datetime

import numpy as np
from sqlmodel import Session, col, select

from app.core.config import settings
from app.models.rag import Document, DocumentChunk
from app.rag.access import ReadScope
from app.rag.effective_date import document_version_status, ensure_utc
from app.rag.index_identity import EmbeddingIndexIdentity, IndexIdentityError
from app.rag.index_registry import legacy_chunk_count, resolve_read_index_for
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
        read_scope: ReadScope | None = None,
        identity: EmbeddingIndexIdentity | None = None,
    ) -> list[ChunkResult]:
        # 方法体全是同步 SQLModel / numpy / BM25，不能直接在事件循环线程里跑：
        # 否则外层 native.retrieve 的 asyncio.wait_for 定时器在同步阻塞期间无法
        # 触发，慢查询会把调用方拖满而不是被限时放弃（RAG-038 复审 P1a）。
        # 丢进工作线程后，wait_for 才能在 deadline 到点时让协程放弃等待。
        # database.py 已对 SQLite 设 check_same_thread=False；一次检索内该
        # request-scoped Session 只在本线程被触碰，事件循环 await 期间不并发使用它，
        # 因此线程安全成立。权限过滤（tenant/read_scope/index_id）逻辑在同步方法内原样保留。
        return await asyncio.to_thread(
            self._hybrid_search_sync,
            query_embedding,
            query_tokens,
            tenant_id,
            top_k,
            rrf_k,
            as_of=as_of,
            schedule_at=schedule_at,
            read_scope=read_scope,
            identity=identity,
        )

    def _hybrid_search_sync(
        self,
        query_embedding: list[float],
        query_tokens: list[str],
        tenant_id: str,
        top_k: int,
        rrf_k: int = 60,
        as_of: datetime | None = None,
        schedule_at: datetime | None = None,
        read_scope: ReadScope | None = None,
        identity: EmbeddingIndexIdentity | None = None,
    ) -> list[ChunkResult]:
        stmt = (
            select(DocumentChunk)
            .join(Document, col(Document.id) == col(DocumentChunk.document_id))
            .where(DocumentChunk.tenant_id == tenant_id)
        )
        stmt = stmt.where(col(Document.deleted_at).is_(None))
        # 索引身份核对（ADR-0008）：只检索当前生效索引名下的分块，
        # 且查询 provider 的完整身份必须与登记一致（同维异模型靠维度发现不了）。
        # 身份未知的历史分块（index_id 为 NULL）不参与，也不静默跳过——
        # 它们的数量由 health 暴露，需要人工登记或重建后才能纳入。
        read_index = resolve_read_index_for(
            self.session, identity, tenant_id=tenant_id, backend="local"
        )
        if read_index is None:
            # 空库：确实没有向量，按 no_hit 处理。不是故障，不必报 unavailable。
            logger.debug("no_active_embedding_index_empty_library backend=local tenant=%s", tenant_id)
            return []
        if len(query_embedding) != read_index.dim:
            raise IndexIdentityError(
                f"查询向量维度 {len(query_embedding)} 与生效索引 {read_index.name} "
                f"登记的 dim={read_index.dim} 不一致，拒绝混用"
            )
        stmt = stmt.where(col(DocumentChunk.index_id) == read_index.id)
        # 鉴权主体的有效读范围必须在取候选时就生效，否则他人文档会先占位再被丢弃。
        if read_scope is not None and read_scope.uploader_id is not None:
            stmt = stmt.where(col(Document.user_id) == read_scope.uploader_id)
        if not settings.RAG_EFFECTIVE_DATE_FILTER:
            stmt = stmt.where(col(Document.is_current).is_(True))
        rows = self.session.exec(stmt).all()
        if not rows:
            # 过滤后为空且确实存在被隔离的历史块：这是「升级后查不到」的典型现场，
            # 必须 warning，不能静默成一个普通空结果。
            quarantined = legacy_chunk_count(self.session, tenant_id=tenant_id)
            if quarantined:
                logger.warning(
                    "rag_no_candidate_in_active_index backend=local tenant=%s active=%s "
                    "quarantined_legacy_chunks=%s reason=history_not_adopted",
                    tenant_id, read_index.name, quarantined,
                )
            return []
        rows, version_by_chunk = visible_chunks_with_status(
            self.session, rows, as_of, schedule_at
        )
        if not rows:
            return []

        # 以索引登记的维度为准，而不是以本次查询向量的长度为准：
        # 同维异模型时长度相同，按长度判断根本发现不了混用。
        expected_dim = read_index.dim
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
                    similarity=float(dense[idx]),
                )
            )
        return results


def visible_chunks_with_status(
    session: Session,
    rows: Sequence[DocumentChunk],
    as_of: datetime | None,
    schedule_at: datetime | None,
    *,
    documents: dict[str, Document] | None = None,
) -> tuple[list[DocumentChunk], dict[str, str]]:
    if not settings.RAG_EFFECTIVE_DATE_FILTER:
        return list(rows), {row.id: "current" for row in rows}
    moment = ensure_utc(as_of) if as_of is not None else datetime.now(UTC)
    visible: list[DocumentChunk] = []
    version_by_chunk: dict[str, str] = {}
    doc_cache: dict[str, Document | None] = dict(documents or {})
    for row in rows:
        if row.document_id not in doc_cache:
            if documents is not None:
                continue
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
