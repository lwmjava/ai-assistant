"""RAG 服务：串联摄取、检索与文档管理，并对接 Agent 管线。

职责：
- 文档摄取：文本分块 → 批量嵌入 → 持久化为 Document / DocumentChunk；
- 混合检索：将查询转为向量与词项，调用向量库融合检索；
- 文档生命周期：列表 / 详情 / 删除（级联删除分块与向量索引）；
- 生成管线可用的检索钩子（``make_retriever``）。

所有读取与写入均按 ``tenant_id`` 隔离；系统管理员可见同租户全部文档，
普通用户仅能操作自己创建的文档。
"""

import json
import logging
import os
import uuid
from collections.abc import Callable

from sqlmodel import Session, select

from app.core.config import settings
from app.models.rag import Document, DocumentChunk
from app.models.user import User
from app.rag.backend.base import RagBackend
from app.rag.backend.factory import get_rag_backend, normalize_rag_backend
from app.rag.chunking.base import ChunkParams
from app.rag.chunking.factory import get_chunking_strategy, resolve_strategy_name
from app.rag.document_parsers.base import ParsedDocument
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.embeddings.mock import tokenize
from app.rag.retriever import HybridRetriever
from app.rag.vectorstore.base import ChunkResult, VectorStore
from app.rag.vectorstore.factory import get_vector_store

logger = logging.getLogger(__name__)

# 单次批量嵌入的最大文本数，避免超长文档一次性压垮嵌入接口。
_EMBED_BATCH = 32

Tokenizer = Callable[[str], list[str]]


class RAGService:
    """检索增强生成服务（会话级，绑定一个数据库会话与租户）。"""

    def __init__(
        self,
        session: Session,
        tenant_id: str,
        embedding_provider: EmbeddingProvider | None = None,
        vector_store: VectorStore | None = None,
        *,
        tokenizer: Tokenizer | None = None,
        backend: RagBackend | None = None,
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self._embedding = embedding_provider or get_embedding_provider()
        self._vector_store = vector_store or get_vector_store(session)
        self._tokenizer = tokenizer or tokenize
        self._default_backend_name = settings.RAG_BACKEND
        self._backend = backend or get_rag_backend(
            self._embedding,
            self._vector_store,
            backend=self._default_backend_name,
            tokenizer=self._tokenizer,
            rrf_k=settings.RAG_HYBRID_RRF_K,
        )

    def _resolve_backend(self, backend: str | None = None) -> RagBackend:
        """按请求级覆盖或默认配置返回后端实例。"""
        if backend is None:
            return self._backend
        normalized = normalize_rag_backend(backend)
        if normalized == self._backend.name:
            return self._backend
        return get_rag_backend(
            self._embedding,
            self._vector_store,
            backend=normalized,
            tokenizer=self._tokenizer,
            rrf_k=settings.RAG_HYBRID_RRF_K,
        )

    # ── 摄取 ────────────────────────────────────────
    def _build_chunk_params(self, chunk_params: dict | None = None) -> ChunkParams:
        """构造切分参数，合并全局默认值与请求级覆盖。"""
        return ChunkParams(
            chunk_size=settings.RAG_CHUNK_SIZE,
            chunk_overlap=settings.RAG_CHUNK_OVERLAP,
            **(chunk_params or {}),
        )

    async def _persist_document(
        self,
        *,
        title: str,
        source: str | None,
        user_id: str,
        chunk_objs: list,
        strategy_name: str,
        storage_path: str | None = None,
        source_kind: str | None = None,
        source_uri: str | None = None,
        content_hash: str | None = None,
        version_group_id: str | None = None,
        version_number: int = 1,
        previous_document_id: str | None = None,
        import_job_id: str | None = None,
    ) -> Document:
        """将切分结果持久化为文档与分块。"""
        if not chunk_objs:
            raise ValueError("文本为空或无法切分为任何分块")

        document = Document(
            tenant_id=self.tenant_id,
            user_id=user_id,
            title=title,
            source=source,
            storage_path=storage_path,
            source_kind=source_kind or "file",
            source_uri=source_uri,
            content_hash=content_hash,
            version_group_id=version_group_id or uuid.uuid4().hex,
            version_number=version_number,
            previous_document_id=previous_document_id,
            import_job_id=import_job_id,
            chunk_count=0,
        )
        self.session.add(document)
        self.session.commit()
        self.session.refresh(document)

        chunk_rows: list[DocumentChunk] = []
        parent_id_map: dict[str, str] = {}
        for chunk_index, chunk in enumerate(chunk_objs):
            # 手动生成主键，便于父块落库后直接回填子块的 parent_id，无需逐块 flush。
            row_id = uuid.uuid4().hex
            parent_id = None
            if chunk.parent_id and chunk.parent_id in parent_id_map:
                parent_id = parent_id_map[chunk.parent_id]
            row = DocumentChunk(
                id=row_id,
                tenant_id=self.tenant_id,
                document_id=document.id,
                chunk_index=chunk_index,
                content=chunk.text,
                source=source,
                tokens=json.dumps(self._tokenizer(chunk.text), ensure_ascii=False),
                strategy=strategy_name,
                chunk_metadata=json.dumps(chunk.metadata, ensure_ascii=False)
                if chunk.metadata
                else None,
                parent_id=parent_id,
            )
            if chunk.metadata.get("kind") == "parent":
                parent_id_map[str(chunk.metadata.get("parent_key"))] = row_id
            chunk_rows.append(row)
            self.session.add(row)

        await self._embed_and_store(chunk_rows)
        document.chunk_count = len(chunk_rows)
        self.session.add(document)
        self.session.commit()
        self.session.refresh(document)
        return document

    async def ingest_text(
        self,
        text: str,
        title: str,
        source: str | None,
        user_id: str,
        *,
        storage_path: str | None = None,
        backend: str | None = None,
        source_kind: str | None = None,
        source_uri: str | None = None,
        content_hash: str | None = None,
        version_group_id: str | None = None,
        version_number: int = 1,
        previous_document_id: str | None = None,
        import_job_id: str | None = None,
        strategy: str | None = None,
        chunk_params: dict | None = None,
    ) -> Document:
        """摄取一段文本：切分、嵌入、落库，返回文档记录。

        ``strategy`` 为请求级切分策略名，``chunk_params`` 为策略专用参数，
        均可缺省；缺省时由 ``resolve_strategy_name`` 依据配置与文本特征路由。
        ``backend`` 参数为历史兼容保留，切分已统一走策略层，不再受其影响。
        """
        strategy_name = resolve_strategy_name(text, strategy)
        chunking = get_chunking_strategy(strategy_name, embedding=self._embedding)
        params = self._build_chunk_params(chunk_params)
        chunk_objs = await chunking.split(text, params=params)
        return await self._persist_document(
            title=title,
            source=source,
            user_id=user_id,
            chunk_objs=chunk_objs,
            strategy_name=strategy_name,
            storage_path=storage_path,
            source_kind=source_kind,
            source_uri=source_uri,
            content_hash=content_hash,
            version_group_id=version_group_id,
            version_number=version_number,
            previous_document_id=previous_document_id,
            import_job_id=import_job_id,
        )

    async def ingest_parsed_document(
        self,
        parsed: ParsedDocument,
        *,
        user_id: str,
        title: str | None = None,
        source: str | None = None,
        storage_path: str | None = None,
        backend: str | None = None,
        source_kind: str | None = None,
        source_uri: str | None = None,
        content_hash: str | None = None,
        version_group_id: str | None = None,
        version_number: int = 1,
        previous_document_id: str | None = None,
        import_job_id: str | None = None,
        strategy: str | None = None,
        chunk_params: dict | None = None,
    ) -> Document:
        """摄取解析结果；结构感知策略可直接消费 parser 保留的 blocks。"""
        chosen_title = title or parsed.title
        chosen_source = parsed.source if source is None else source
        strategy_name = resolve_strategy_name(parsed.text, strategy)
        chunking = get_chunking_strategy(strategy_name, embedding=self._embedding)
        params = self._build_chunk_params(chunk_params)
        if strategy_name in {"format_aware", "layout_aware"} and parsed.blocks:
            chunk_objs = await chunking.split_blocks(parsed.blocks, params=params)
        else:
            chunk_objs = await chunking.split(parsed.text, params=params)
        return await self._persist_document(
            title=chosen_title,
            source=chosen_source,
            user_id=user_id,
            chunk_objs=chunk_objs,
            strategy_name=strategy_name,
            storage_path=storage_path,
            source_kind=source_kind,
            source_uri=source_uri,
            content_hash=content_hash,
            version_group_id=version_group_id,
            version_number=version_number,
            previous_document_id=previous_document_id,
            import_job_id=import_job_id,
        )

    async def ingest_file(self, path: str, title: str | None, user_id: str) -> Document:
        """摄取本地文本文件（支持 .txt / .md）。"""
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".txt", ".md"):
            raise ValueError("仅支持 .txt / .md 文本文件摄取")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        source = title or os.path.basename(path)
        return await self.ingest_text(text, source, source, user_id)

    async def _embed_and_store(self, rows: list[DocumentChunk]) -> None:
        for i in range(0, len(rows), _EMBED_BATCH):
            batch = rows[i : i + _EMBED_BATCH]
            vectors = await self._embedding.embed([r.content for r in batch])
            for row, vec in zip(batch, vectors):
                row.embedding = json.dumps(vec, ensure_ascii=False)
        self.session.commit()

    # ── 检索 ────────────────────────────────────────
    async def search(
        self, query: str, top_k: int | None = None, *, backend: str | None = None
    ) -> list[ChunkResult]:
        """对查询做混合检索，命中子块时展开父块上下文并去重。"""
        top_k = top_k or settings.RAG_TOP_K
        rag_backend = self._resolve_backend(backend)
        hits = await rag_backend.retrieve(
            query, tenant_id=self.tenant_id, top_k=top_k
        )
        return await self._expand_parent_chunks(hits)

    async def _expand_parent_chunks(
        self, hits: list[ChunkResult]
    ) -> list[ChunkResult]:
        """命中子块时，追加其父块并去重，保留原排序与评分。"""
        expanded: list[ChunkResult] = []
        seen: set[str] = set()
        for hit in hits:
            if hit.id not in seen:
                seen.add(hit.id)
                expanded.append(hit)
            row = self.session.get(DocumentChunk, hit.id)
            if row is not None and row.parent_id:
                parent_row = self.session.get(DocumentChunk, row.parent_id)
                if parent_row is not None and parent_row.id not in seen:
                    seen.add(parent_row.id)
                    expanded.append(
                        ChunkResult(
                            id=parent_row.id,
                            content=parent_row.content,
                            source=parent_row.source,
                            document_id=parent_row.document_id,
                            score=hit.score,
                        )
                    )
        return expanded

    def make_retriever(self, top_k: int | None = None) -> HybridRetriever:
        """生成可注入 Agent 管线的混合检索器。"""
        return HybridRetriever(
            self._backend,
            self.tenant_id,
            top_k or settings.RAG_TOP_K,
        )

    # ── 文档管理 ────────────────────────────────────
    def _can_access(self, doc: Document, user: User) -> bool:
        if user.role_enum.value == "system_admin":
            return doc.tenant_id == user.tenant_id
        return doc.user_id == user.id and doc.tenant_id == user.tenant_id

    def list_documents(self, user: User) -> list[Document]:
        """列出当前用户可见的文档（系统管理员可见同租户全部）。"""
        stmt = select(Document).where(
            Document.tenant_id == user.tenant_id,
            Document.is_current.is_(True),
        )
        if user.role_enum.value != "system_admin":
            stmt = stmt.where(Document.user_id == user.id)
        stmt = stmt.order_by(Document.updated_at.desc())
        return list(self.session.exec(stmt).all())

    def get_document(self, document_id: str, user: User) -> Document | None:
        """按 ID 获取文档，无权限时返回 None。"""
        doc = self.session.get(Document, document_id)
        if doc is None or not self._can_access(doc, user):
            return None
        return doc

    async def delete_document(self, document_id: str, user: User) -> bool:
        """删除文档（清理向量索引，再级联删除分块与主库记录）。

        必须先清理向量再删主库记录：分块随 Document 级联删除后，
        向量库里已无从得知该文档关联哪些分块，残留向量会持续被检索命中。
        向量清理失败只记日志不阻断，避免数据库记录无法删除而成为孤儿数据。
        """
        doc = self.get_document(document_id, user)
        if doc is None:
            return False
        try:
            removed = await self._vector_store.delete_by_document(document_id, self.tenant_id)
            logger.info("文档向量已清理: document=%s, removed=%s", document_id, removed)
        except Exception:  # noqa: BLE001 — 向量清理失败不应阻断主库删除
            logger.exception("清理文档向量失败: document=%s", document_id)
        self.session.delete(doc)
        self.session.commit()
        return True
