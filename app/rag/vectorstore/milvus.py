"""Milvus 向量库实现（可选）。

当 ``RAG_VECTOR_STORE=milvus`` 时启用，提供可水平扩展的稠密向量检索。
本模块不在导入时依赖 ``pymilvus``，仅在构造 / 调用时按需导入，
因此未安装该依赖时仍可正常 Import 本文件（本地向量库不受影响）。

设计：稠密检索走 Milvus，稀疏检索（BM25）与内容存储仍复用主库中的
``DocumentChunk``（已保存 tokens / content），两者通过 RRF 融合。
仅在确实需要大规模向量检索时使用；中小规模直接采用本地实现即可。

调用韧性（RAG-038）：Milvus 查询套**共享**总时限（``current_or_new_deadline()``）。
这里不做重试——向量检索重试的收益不确定，而重复查询会放大服务端压力；
限时失败同样向上抛（``MilvusUnavailableError``），绝不返回空列表冒充「没有命中」。

⚠️ 取消边界：pymilvus 是**同步**客户端，查询在线程池里执行。总时限到期或调用
被取消时，只能让协程放弃等待，**无法真正中断**线程里的底层调用——线程会继续
跑到底才释放工作线程。因此取消发生后本方法不会返回任何结果，也不会把
``CancelledError`` 改写成业务异常，而是原样上抛。
"""

import json
import logging
from datetime import datetime

import numpy as np
from sqlmodel import Session, col, select

from app.core.config import settings
from app.models.rag import Document, DocumentChunk, EmbeddingIndex, IndexStatus
from app.rag.access import ReadScope
from app.rag.index_identity import EmbeddingIndexIdentity, IndexIdentityError, identity_matches_row
from app.rag.index_registry import IndexUnavailableError, resolve_read_index_for
from app.rag.resilience import (
    DeadlineExceededError,
    current_or_new_deadline,
    run_blocking_with_deadline,
)
from app.rag.vectorstore.base import ChunkResult, VectorStore
from app.rag.vectorstore.local import _bm25_scores, _rrf, visible_chunks_with_status

logger = logging.getLogger(__name__)

# 集合中的分块归属字段：删除分块时用它定位，缺失会导致向量残留。
_DOCUMENT_ID_FIELD = "document_id"
# 索引归属字段：身份换了必须换集合，但同一集合内仍要能按索引过滤，
# 否则 SQL 回查会拿到别的索引名下的分块。
_INDEX_ID_FIELD = "index_id"


class MilvusUnavailableError(RuntimeError):
    """未安装 pymilvus 或无法连接 Milvus 时抛出。"""


class MilvusVectorStore(VectorStore):
    """基于 Milvus 的向量库实现（稠密检索 + 主库 BM25 融合）。"""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._collection = None
        self._collection_name = ""

    def _resolve_index(
        self, identity: EmbeddingIndexIdentity | None = None, *, tenant_id: str | None = None
    ) -> EmbeddingIndex:
        """Milvus 访问的集合由**登记的生效索引**决定，不再固定用配置里的集合名。

        身份变了集合名就变（``index_name()`` 带版本与身份指纹），因此不同模型
        不可能原地混写同一个集合。
        """
        index = resolve_read_index_for(
            self.session, identity, tenant_id=tenant_id, backend="milvus"
        )
        if index is None:
            raise IndexUnavailableError(
                "Milvus 后端没有生效的 embedding 索引，无法确定操作哪个集合；"
                "请先登记并激活索引（rebuild/activate）。"
            )
        return index

    def _connect(
        self,
        identity: EmbeddingIndexIdentity | None = None,
        index: EmbeddingIndex | None = None,
    ):
        target_index = index if index is not None else self._resolve_index(identity)
        if self._collection is not None and self._collection_name == target_index.name:
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
        name = target_index.name
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="tenant_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name=_DOCUMENT_ID_FIELD, dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name=_INDEX_ID_FIELD, dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(
                name="embedding",
                dtype=DataType.FLOAT_VECTOR,
                dim=target_index.dim,
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
        self._collection_name = name
        return collection

    def _verify_schema(self, collection, name: str) -> None:
        """校验既有集合含有按文档删除、按索引过滤所需的字段。

        早期版本的集合缺少 ``document_id``，此时删除过滤表达式必然失败，
        若不显式报错会导致向量永久残留且无任何提示；缺少 ``index_id`` 则无法
        把候选限定在当前生效索引内，等价于把旧身份向量重新召回。

        Args:
            collection: 已加载的 Milvus 集合。
            name: 集合名，用于错误信息。

        Raises:
            MilvusUnavailableError: 集合缺少 ``document_id`` / ``index_id`` 字段时抛出。
        """
        existing = {f.name for f in (collection.schema.fields or [])}
        missing = [f for f in (_DOCUMENT_ID_FIELD, _INDEX_ID_FIELD) if f not in existing]
        if missing:
            raise MilvusUnavailableError(
                f"Milvus 集合 {name} 缺少 {', '.join(missing)} 字段，无法按文档删除向量"
                "或按索引身份过滤。请重建集合或迁移 schema 后重试"
                f"（当前字段：{sorted(existing)}）。"
            )

    def _ensure_index(self, collection) -> None:
        """确保向量字段已建索引，否则检索会退化为全表扫描甚至报错。"""
        try:
            indexed = {idx.field_name for idx in collection.indexes}
        except Exception:  # noqa: BLE001 - 无索引时接口可能抛错，按未建处理
            indexed = set()
        if "embedding" in indexed:
            return
        index_params: dict = {
            "index_type": settings.MILVUS_INDEX_TYPE,
            "metric_type": "COSINE",
        }
        if "IVF" in settings.MILVUS_INDEX_TYPE.upper():
            index_params["params"] = {"nlist": 128}
        collection.create_index(
            field_name="embedding",
            index_params=index_params,
        )
        logger.info("rag_vector_index_created backend=milvus")

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

    def _write_target(
        self, identity: EmbeddingIndexIdentity | None, target_index_id: str | None
    ) -> EmbeddingIndex:
        if target_index_id is None:
            return self._resolve_index(identity)
        target = self.session.get(EmbeddingIndex, target_index_id)
        if (target is None or target.backend != "milvus"
                or target.status not in (IndexStatus.ACTIVE.value, IndexStatus.PREPARING.value)):
            raise IndexIdentityError("远端写入目标必须是已登记的 active/preparing Milvus 索引")
        if identity is None or not identity_matches_row(identity, target):
            raise IndexIdentityError("远端写入目标与当前模型完整身份不匹配")
        return target

    def validate_add(
        self, chunks: list, identity: EmbeddingIndexIdentity | None = None,
        *, target_index_id: str | None = None,
    ) -> tuple[EmbeddingIndex, list[dict]]:
        """纯SQL/内存预检；调用方可以在任何远端清理前验证整批候选。"""
        target_index = self._write_target(identity, target_index_id)
        # 全批校验完成才连接/写远端，避免前半批已写而后半批才发现身份或维度错误。
        entities: list[dict] = []
        for chunk in chunks:
            if target_index_id is not None:
                doc = self.session.get(Document, chunk.document_id)
                if (chunk.index_id != target_index.id or doc is None
                        or doc.tenant_id != chunk.tenant_id or doc.deleted_at is not None):
                    raise IndexIdentityError("远端分块目标/文档/租户归属不匹配")
            if not chunk.embedding:
                continue
            vector = json.loads(chunk.embedding)
            if (not isinstance(vector, list) or len(vector) != target_index.dim
                    or not all(isinstance(value, int | float) and np.isfinite(value) for value in vector)):
                raise IndexIdentityError("远端分块向量维度或数值与登记索引不匹配")
            entities.append({
                "id": chunk.id,
                "tenant_id": chunk.tenant_id,
                _DOCUMENT_ID_FIELD: chunk.document_id,
                _INDEX_ID_FIELD: target_index.id,
                "embedding": vector,
            })
        return target_index, entities

    async def add(
        self, chunks: list, identity: EmbeddingIndexIdentity | None = None,
        *, target_index_id: str | None = None,
    ) -> None:
        target_index, entities = self.validate_add(chunks, identity, target_index_id=target_index_id)
        if entities:
            collection = self._connect(identity, index=target_index)
            collection.upsert(entities)

    async def delete_by_document(
        self, document_id: str, tenant_id: str, *,
        target_index_id: str | None = None, identity: EmbeddingIndexIdentity | None = None,
    ) -> int:
        """删除某文档的全部分块向量，返回实际删除条数。

        先按主键查出命中条数再删除，因为 Milvus 的 ``delete`` 返回值不含删除计数，
        早期实现直接 ``return len([document_id])`` 恒为 1，会掩盖删除失败。
        """
        target_index = self._write_target(identity, target_index_id)
        if target_index_id is not None:
            doc = self.session.get(Document, document_id)
            if doc is None or doc.tenant_id != tenant_id:
                raise IndexIdentityError("远端清理文档与租户归属不匹配")
        collection = self._connect(identity, index=target_index)
        expr = (
            f'{_DOCUMENT_ID_FIELD} == "{document_id}" and tenant_id == "{tenant_id}"'
            f' and {_INDEX_ID_FIELD} == "{target_index.id}"'
        )
        try:
            query_options = {"consistency_level": "Strong"} if target_index_id is not None else {}
            matched = collection.query(expr=expr, output_fields=["id"], **query_options)
            ids = [row.get("id") for row in matched or []]
            if not ids:
                return 0
            collection.delete(expr=expr)
        except Exception as exc:  # noqa: BLE001 - 集合不可用时降级为未删除
            logger.warning(
                "rag_vector_delete_failed backend=milvus document=%s exception_type=%s",
                document_id,
                type(exc).__name__,
            )
            if target_index_id is not None:
                # 重建路径必须显式失败，不能把清理失败伪装成目标集合本来为空。
                raise MilvusUnavailableError("重建目标远端清理失败，需要补偿") from exc
            return 0
        return len(ids)

    async def count(self, tenant_id: str) -> int:
        stmt = select(DocumentChunk).where(DocumentChunk.tenant_id == tenant_id)
        return len(self.session.exec(stmt).all())

    def _remote_search(
        self,
        identity: EmbeddingIndexIdentity | None,
        index: EmbeddingIndex,
        expr: str,
        query_embedding: list[float],
        expand: int,
    ) -> list:
        """真正打到 Milvus 的那一段（同步），由 ``run_blocking_with_deadline`` 限时。

        连集合、取检索参数、发起查询都在这里面：``connections.connect`` 同样会
        阻塞，不该由调用方各自承担，否则「总时限」只管住查询却管不住连接。
        索引行由调用方解析好传进来，避免在线程池里再碰一次数据库会话。
        """
        collection = self._connect(identity, index=index)
        return collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=self._search_params(collection),
            limit=expand,
            expr=expr,
            output_fields=["id"],
        )[0]

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
        # 身份核对必须在取候选之前：没有生效索引 → IndexUnavailableError，
        # 身份不符 → IndexIdentityError（同维异模型靠维度发现不了）。
        target_index = self._resolve_index(identity, tenant_id=tenant_id)
        expr = (
            f'tenant_id == "{tenant_id}"'
            f' and {_INDEX_ID_FIELD} == "{target_index.id}"'
        )
        expand = max(top_k * 4, 20)
        deadline = current_or_new_deadline()
        try:
            hits = await run_blocking_with_deadline(
                self._remote_search,
                identity,
                target_index,
                expr,
                query_embedding,
                expand,
                deadline=deadline,
                backend="milvus",
                tenant=tenant_id,
            )
        except DeadlineExceededError as exc:
            # 超时也算「没查完」，不能回空列表冒充「知识库里没有」。
            raise MilvusUnavailableError(f"Milvus 检索超时：{exc}") from exc
        except Exception as exc:  # pragma: no cover - 依赖线上 Milvus
            # 不能吞成空列表：那会让调用方把「检索服务故障」当成「知识库里没有」，
            # 用户看到的是一个正常的空结果而不是故障提示。向上抛，由检索状态
            # 契约统一收敛为 unavailable。
            logger.error(
                "rag_vector_search_failed backend=milvus fallback=raise exception_type=%s",
                type(exc).__name__,
            )
            raise MilvusUnavailableError(f"Milvus 检索失败：{type(exc).__name__}") from exc

        candidate_ids = [h.entity.get("id") for h in hits]
        # 候选集下面还会被 SQL 回查收窄（旧索引残留、读范围、可见性），
        # 因此先把 Milvus 的距离按 id 留存，避免收窄后拿不到真实相似度。
        similarity_by_id = {h.entity.get("id"): float(h.distance) for h in hits}
        if not candidate_ids:
            return []

        # 集合里可能残留旧索引的向量，SQL 回查必须再按 index_id 收一次口：
        # 否则旧身份的分块会被重新召回，身份过滤形同虚设。
        stmt = (
            select(DocumentChunk)
            .join(Document, col(Document.id) == col(DocumentChunk.document_id))
            .where(DocumentChunk.id.in_(candidate_ids))  # type: ignore[attr-defined]
            .where(col(DocumentChunk.index_id) == target_index.id)
        )
        stmt = stmt.where(col(Document.deleted_at).is_(None))
        # 集合里没有上传者字段，读范围只能在候选回查阶段生效：
        # 结果尚未成形，因此仍是检索前过滤；但向量候选数不受影响，
        # 极端情况下可用候选少于本地实现，这一点记录在实现说明里。
        if read_scope is not None and read_scope.uploader_id is not None:
            stmt = stmt.where(col(Document.user_id) == read_scope.uploader_id)
        if not settings.RAG_EFFECTIVE_DATE_FILTER:
            stmt = stmt.where(col(Document.is_current).is_(True))
        rows = self.session.exec(stmt).all()
        rows, version_by_chunk = visible_chunks_with_status(
            self.session, rows, as_of, schedule_at
        )
        rows_by_id = {r.id: r for r in rows}
        ordered = [rows_by_id[i] for i in candidate_ids if i in rows_by_id]

        tokens = [json.loads(r.tokens) if r.tokens else [] for r in ordered]
        bm25 = _bm25_scores(query_tokens, tokens)
        if any(s > 0 for s in bm25):
            sparse_order = list(np.argsort(-np.array(bm25)).tolist())
        else:
            # 没有词面证据时仅保留稠密贡献，不构造第二路排名。
            sparse_order = []
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
                    version_status=version_by_chunk.get(row.id, "current"),
                    # 集合以 COSINE 为检索度量，Milvus 返回的 distance 即余弦相似度（越大越近）。
                    similarity=similarity_by_id.get(row.id, 0.0),
                )
            )
        return results
