"""Milvus 向量库实现（可选）。

当 ``RAG_VECTOR_STORE=milvus`` 时启用，提供可水平扩展的稠密向量检索。
本模块不在导入时依赖 ``pymilvus``，仅在构造 / 调用时按需导入，
因此未安装该依赖时仍可正常 Import 本文件（本地向量库不受影响）。

设计：稠密检索走 Milvus，稀疏检索（BM25）与内容存储仍复用主库中的
``DocumentChunk``（已保存 tokens / content），两者通过 RRF 融合。
仅在确实需要大规模向量检索时使用；中小规模直接采用本地实现即可。
"""

import json
import logging

from sqlmodel import Session, select

from app.core.config import settings
from app.models.rag import DocumentChunk
from app.rag.vectorstore.base import ChunkResult, VectorStore
from app.rag.vectorstore.local import _bm25_scores, _rrf

logger = logging.getLogger(__name__)


class MilvusUnavailableError(RuntimeError):
    """未安装 pymilvus 或无法连接 Milvus 时抛出。"""


class MilvusVectorStore(VectorStore):
    """基于 Milvus 的向量库实现（稠密检索 + 主库 BM25 融合）。"""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._collection = None

    def _connect(self):
        if self._collection is not None:
            return self._collection
        try:
            from pymilvus import (  # type: ignore
                Collection,
                CollectionSchema,
                DataType,
                FieldSchema,
                connections,
            )
        except ImportError as exc:  # pragma: no cover - 仅在未安装时触发
            raise MilvusUnavailableError(
                "未安装 pymilvus，无法使用 Milvus 向量库；"
                "请执行 `pip install pymilvus` 或改用本地向量库（RAG_VECTOR_STORE=local）。"
            ) from exc

        connections.connect(
            alias="default",
            uri=settings.MILVUS_URI,
            token=settings.MILVUS_TOKEN or None,
        )
        name = settings.MILVUS_COLLECTION
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="tenant_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(
                name="embedding",
                dtype=DataType.FLOAT_VECTOR,
                dim=settings.EMBEDDING_DIM,
            ),
        ]
        schema = CollectionSchema(fields, description="ai-assistant 文档分块向量")
        try:
            collection = Collection(name, schema)
        except Exception:  # 已存在则直接加载
            collection = Collection(name)
        collection.load()
        self._collection = collection
        return collection

    async def add(self, chunks: list) -> None:
        collection = self._connect()
        entities = [
            {
                "id": c.id,
                "tenant_id": c.tenant_id,
                "embedding": json.loads(c.embedding) if c.embedding else None,
            }
            for c in chunks
            if c.embedding
        ]
        if entities:
            collection.upsert(entities)

    async def delete_by_document(self, document_id: str, tenant_id: str) -> int:
        collection = self._connect()
        expr = f'document_id == "{document_id}" and tenant_id == "{tenant_id}"'
        try:
            collection.delete(expr)
        except Exception:  # 集合为空或字段未建索引时静默跳过
            logger.warning("Milvus 删除文档分块失败（可能集合为空）：%s", document_id)
        return len([document_id])

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
        collection = self._connect()
        expr = f'tenant_id == "{tenant_id}"'
        expand = max(top_k * 4, 20)
        search_params = {"metric_type": "COSINE", "params": {"nprobe": 16}}
        try:
            hits = collection.search(
                data=[query_embedding],
                anns_field="embedding",
                param=search_params,
                limit=expand,
                expr=expr,
                output_fields=["id"],
            )[0]
        except Exception:  # pragma: no cover - 依赖线上 Milvus
            logger.warning("Milvus 稠密检索失败，回退为空结果。")
            return []

        candidate_ids = [h.entity.get("id") for h in hits]
        if not candidate_ids:
            return []

        rows = self.session.exec(
            select(DocumentChunk).where(DocumentChunk.id.in_(candidate_ids))  # type: ignore[attr-defined]
        ).all()
        rows_by_id = {r.id: r for r in rows}
        ordered = [rows_by_id[i] for i in candidate_ids if i in rows_by_id]

        tokens = [json.loads(r.tokens) if r.tokens else [] for r in ordered]
        bm25 = _bm25_scores(query_tokens, tokens)
        if any(s > 0 for s in bm25):
            sparse_order = list(
                __import__("numpy").argsort(-__import__("numpy").array(bm25)).tolist()
            )
        else:
            sparse_order = list(range(len(ordered)))
        dense_order = list(range(len(ordered)))  # 已是按距离升序

        fused = _rrf([dense_order, sparse_order], k=rrf_k)
        results: list[ChunkResult] = []
        for idx, score in fused[:top_k]:
            row = ordered[idx]
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
