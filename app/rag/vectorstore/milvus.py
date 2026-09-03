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

import numpy as np
from sqlmodel import Session, select

from app.core.config import settings
from app.models.rag import DocumentChunk
from app.rag.vectorstore.base import ChunkResult, VectorStore
from app.rag.vectorstore.local import _bm25_scores, _rrf

logger = logging.getLogger(__name__)

# 集合中的分块归属字段：删除分块时用它定位，缺失会导致向量残留。
_DOCUMENT_ID_FIELD = "document_id"


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
            FieldSchema(name=_DOCUMENT_ID_FIELD, dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(
                name="embedding",
                dtype=DataType.FLOAT_VECTOR,
                dim=settings.EMBEDDING_DIM,
            ),
        ]
        schema = CollectionSchema(fields, description="ai-assistant 文档分块向量")
        try:
            collection = Collection(name, schema)
            created = True
        except Exception:  # noqa: BLE001 - 集合已存在时由下分支加载
            collection = Collection(name)
            created = False

        if not created:
            self._verify_schema(collection, name)
        self._ensure_index(collection)
        collection.load()
        self._collection = collection
        return collection

    def _verify_schema(self, collection, name: str) -> None:
        """校验既有集合含有按文档删除所需的字段。

        早期版本的集合缺少 ``document_id``，此时删除过滤表达式必然失败，
        若不显式报错会导致向量永久残留且无任何提示。

        Args:
            collection: 已加载的 Milvus 集合。
            name: 集合名，用于错误信息。

        Raises:
            MilvusUnavailableError: 集合缺少 ``document_id`` 字段时抛出。
        """
        existing = {f.name for f in (collection.schema.fields or [])}
        if _DOCUMENT_ID_FIELD not in existing:
            raise MilvusUnavailableError(
                f"Milvus 集合 {name} 缺少 {_DOCUMENT_ID_FIELD} 字段，无法按文档删除向量。"
                f"请重建集合或迁移 schema 后重试（当前字段：{sorted(existing)}）。"
            )

    def _ensure_index(self, collection) -> None:
        """确保向量字段已建索引，否则检索会退化为全表扫描甚至报错。"""
        try:
            indexed = {idx.field_name for idx in collection.indexes}
        except Exception:  # noqa: BLE001 - 无索引时接口可能抛错，按未建处理
            indexed = set()
        if "embedding" in indexed:
            return
        collection.create_index(
            field_name="embedding",
            params={
                "index_type": settings.MILVUS_INDEX_TYPE,
                "metric_type": "COSINE",
                "params": {"nlist": 128},
            },
        )
        logger.info("Milvus 集合 %s 已创建向量索引", settings.MILVUS_COLLECTION)

    def _search_params(self, collection) -> dict:
        """按集合实际索引类型构造检索参数。

        ``nprobe`` 仅对 IVF 系列索引有意义；传给 AUTOINDEX 会被忽略甚至报错，
        因此按索引类型决定是否携带。
        """
        index_type = ""
        try:
            for idx in collection.indexes:
                index_type = str((idx.params or {}).get("index_type", "") or "")
                break
        except Exception:  # noqa: BLE001 - 取不到索引信息时按默认参数检索
            index_type = ""
        params = {"nprobe": settings.MILVUS_NPROBE} if "IVF" in index_type.upper() else {}
        return {"metric_type": "COSINE", "params": params}

    async def add(self, chunks: list) -> None:
        collection = self._connect()
        entities = [
            {
                "id": c.id,
                "tenant_id": c.tenant_id,
                _DOCUMENT_ID_FIELD: c.document_id,
                "embedding": json.loads(c.embedding) if c.embedding else None,
            }
            for c in chunks
            if c.embedding
        ]
        if entities:
            collection.upsert(entities)

    async def delete_by_document(self, document_id: str, tenant_id: str) -> int:
        """删除某文档的全部分块向量，返回实际删除条数。

        先按主键查出命中条数再删除，因为 Milvus 的 ``delete`` 返回值不含删除计数，
        早期实现直接 ``return len([document_id])`` 恒为 1，会掩盖删除失败。
        """
        collection = self._connect()
        expr = f'{_DOCUMENT_ID_FIELD} == "{document_id}" and tenant_id == "{tenant_id}"'
        try:
            matched = collection.query(expr=expr, output_fields=["id"])
            ids = [row.get("id") for row in matched or []]
            if not ids:
                return 0
            collection.delete(expr=expr)
        except Exception as exc:  # noqa: BLE001 - 集合不可用时降级为未删除
            logger.warning("Milvus 删除文档分块失败：%s（%s）", document_id, exc)
            return 0
        return len(ids)

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
        try:
            hits = collection.search(
                data=[query_embedding],
                anns_field="embedding",
                param=self._search_params(collection),
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
            sparse_order = list(np.argsort(-np.array(bm25)).tolist())
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
