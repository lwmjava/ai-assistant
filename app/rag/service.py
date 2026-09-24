"""RAG 服务：串联摄取、检索与文档管理，并对接 Agent 管线。

职责：
- 文档摄取：文本分块 → 批量嵌入 → 持久化为 Document / DocumentChunk；
- 混合检索：将查询转为向量与词项，调用向量库融合检索；
- 文档生命周期：列表 / 详情 / 删除（级联删除分块与向量索引）；
- 生成管线可用的检索钩子（``make_retriever``）。

检索面默认同租户当前版本可读。
控制面：成员仅自己的当前版；租户管理员看本租户全部版本；系统管理员可跨租户。
"""

import json
import logging
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.core.config import settings
from app.models.rag import Document, DocumentChunk
from app.models.user import User
from app.rag.access import (
    can_control_document,
    can_read_document,
    can_write_document,
    is_kb_admin,
    restrict_list_to_uploader,
)
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


def demote_other_current_versions(session: Session, version_group_id: str, *, keep_id: str) -> None:
    """同一版本组只保留 keep_id 为当前版。调用方负责提交。"""
    rows = session.exec(
        select(Document).where(
            Document.version_group_id == version_group_id,
            Document.is_current.is_(True),
            Document.id != keep_id,
        )
    ).all()
    for row in rows:
        row.is_current = False
        row.version_state = "replaced"
        session.add(row)

logger = logging.getLogger(__name__)

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
        is_current: bool = True,
        version_state: str | None = None,
        effective_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> Document:
        """将切分结果持久化为文档与分块。"""
        if not chunk_objs:
            raise ValueError("文本为空或无法切分为任何分块")

        # 先做嵌入再开写事务：避免网络调用期间长时间占用 SQLite 写锁，
        # 也避免「文档已提交、分块/向量失败」留下 chunk_count=0 的孤儿记录。
        try:
            embeddings = await self._embed_texts([chunk.text for chunk in chunk_objs])
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
                is_current=is_current,
                version_state=version_state or ("published" if is_current else "replaced"),
                effective_at=effective_at,
                expires_at=expires_at,
                chunk_count=len(chunk_objs),
            )
            self.session.add(document)
            self.session.flush()

            parent_id_map: dict[str, str] = {}
            for chunk_index, (chunk, vector) in enumerate(
                zip(chunk_objs, embeddings, strict=True)
            ):
                # 手动生成主键，便于父块落库后直接回填子块的 parent_id，无需逐块 flush。
                row_id = uuid.uuid4().hex
                parent_id = None
                if chunk.parent_id and chunk.parent_id in parent_id_map:
                    parent_id = parent_id_map[chunk.parent_id]
                metadata = chunk.metadata or {}
                row = DocumentChunk(
                    id=row_id,
                    tenant_id=self.tenant_id,
                    document_id=document.id,
                    chunk_index=chunk_index,
                    content=chunk.text,
                    source=source,
                    embedding=json.dumps(vector, ensure_ascii=False),
                    tokens=json.dumps(self._tokenizer(chunk.text), ensure_ascii=False),
                    strategy=strategy_name,
                    chunk_metadata=json.dumps(metadata, ensure_ascii=False)
                    if metadata
                    else None,
                    parent_id=parent_id,
                )
                if metadata.get("kind") == "parent":
                    parent_id_map[str(metadata.get("parent_key"))] = row_id
                self.session.add(row)

            if document.is_current:
                demote_other_current_versions(
                    self.session, document.version_group_id, keep_id=document.id
                )
            self.session.commit()
            self.session.refresh(document)
            return document
        except Exception:
            self.session.rollback()
            raise

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
        is_current: bool = True,
        version_state: str | None = None,
        effective_at: datetime | None = None,
        expires_at: datetime | None = None,
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
            is_current=is_current,
            version_state=version_state,
            effective_at=effective_at,
            expires_at=expires_at,
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
        is_current: bool = True,
        effective_at: datetime | None = None,
        expires_at: datetime | None = None,
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
            is_current=is_current,
            effective_at=effective_at,
            expires_at=expires_at,
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

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """调用嵌入接口；分批由提供商按厂商上限处理，这里不触碰数据库事务。"""
        vectors = await self._embedding.embed(texts)
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"嵌入结果数量与文本数量不一致：texts={len(texts)} vectors={len(vectors)}"
            )
        return vectors

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
                            version_status=hit.version_status,
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
        """读权限。保留此名以兼容旧调用，语义见 ``can_read_document``。"""
        return can_read_document(doc, user)

    def list_documents(
        self,
        user: User,
        *,
        include_deleted: bool = False,
        version_state: str | None = None,
    ) -> list[Document]:
        """控制面列表。成员只看自己的未删当前版；管理员可看历史版。"""
        from app.core.security import Role

        if user.role_enum == Role.SYSTEM_ADMIN:
            stmt = select(Document)
        elif user.role_enum == Role.TENANT_ADMIN:
            stmt = select(Document).where(Document.tenant_id == user.tenant_id)
        else:
            stmt = select(Document).where(
                Document.tenant_id == user.tenant_id,
                Document.user_id == user.id,
                Document.is_current.is_(True),
            )
            if restrict_list_to_uploader(user):
                stmt = stmt.where(Document.user_id == user.id)
        if not (include_deleted and is_kb_admin(user)):
            stmt = stmt.where(Document.deleted_at.is_(None))
        if version_state and is_kb_admin(user):
            stmt = stmt.where(Document.version_state == version_state)
        stmt = stmt.order_by(Document.updated_at.desc())
        return list(self.session.exec(stmt).all())

    def get_document(self, document_id: str, user: User) -> Document | None:
        """按 ID 获取文档。控制面无权限时返回 None。成员看不到已软删文档。"""
        doc = self.session.get(Document, document_id)
        if doc is None or not can_control_document(doc, user):
            return None
        if doc.deleted_at is not None and not is_kb_admin(user):
            return None
        return doc

    def publish_document(self, document_id: str, user: User) -> Document | None:
        """把目标版本设为当前发布版，并在同一事务里替换同组旧当前版。"""
        doc = self.session.get(Document, document_id)
        if doc is None or not can_control_document(doc, user):
            return None
        if doc.deleted_at is not None or doc.version_state == "archived":
            return None
        if doc.is_current and doc.version_state == "published":
            return doc
        demote_other_current_versions(self.session, doc.version_group_id, keep_id=doc.id)
        doc.is_current = True
        doc.version_state = "published"
        self.session.add(doc)
        self.session.commit()
        self.session.refresh(doc)
        return doc

    def schedule_document(self, document_id: str, user: User, effective_at: datetime) -> Document | None:
        """标为待生效。不自动变成当前版。"""
        doc = self.session.get(Document, document_id)
        if doc is None or not can_control_document(doc, user) or doc.deleted_at is not None:
            return None
        if effective_at <= datetime.now(UTC):
            raise ValueError("待生效时间必须在未来")
        doc.is_current = False
        doc.version_state = "scheduled"
        doc.effective_at = effective_at
        self.session.add(doc)
        self.session.commit()
        self.session.refresh(doc)
        return doc

    def archive_document(self, document_id: str, user: User) -> Document | None:
        """归档并退出当前版。"""
        doc = self.session.get(Document, document_id)
        if doc is None or not can_control_document(doc, user) or doc.deleted_at is not None:
            return None
        doc.is_current = False
        doc.version_state = "archived"
        self.session.add(doc)
        self.session.commit()
        self.session.refresh(doc)
        return doc

    async def reindex_document_in_place(
        self,
        document: Document,
        parsed: ParsedDocument,
        *,
        content_hash: str,
    ) -> Document:
        """就地替换分块与向量，不改变 is_current 与版本组。"""
        strategy_name = resolve_strategy_name(parsed.text, None)
        chunking = get_chunking_strategy(strategy_name, embedding=self._embedding)
        chunk_objs = await chunking.split(parsed.text, params=self._build_chunk_params(None))
        if not chunk_objs:
            raise ValueError("文本为空或无法切分为任何分块")
        embeddings = await self._embed_texts([chunk.text for chunk in chunk_objs])
        try:
            await self._vector_store.delete_by_document(document.id, document.tenant_id)
        except Exception:  # noqa: BLE001 — 旧向量清理失败不阻断就地重建
            logger.exception("重解析前清理向量失败: document=%s", document.id)
        old_chunks = self.session.exec(
            select(DocumentChunk).where(DocumentChunk.document_id == document.id)
        ).all()
        for chunk in old_chunks:
            self.session.delete(chunk)
        self.session.flush()
        parent_id_map: dict[str, str] = {}
        for chunk_index, (piece, vector) in enumerate(zip(chunk_objs, embeddings, strict=True)):
            row_id = uuid.uuid4().hex
            parent_id = None
            if piece.parent_id and piece.parent_id in parent_id_map:
                parent_id = parent_id_map[piece.parent_id]
            metadata: dict = piece.metadata or {}
            row = DocumentChunk(
                id=row_id,
                tenant_id=document.tenant_id,
                document_id=document.id,
                chunk_index=chunk_index,
                content=piece.text,
                source=document.source,
                embedding=json.dumps(vector, ensure_ascii=False),
                tokens=json.dumps(self._tokenizer(piece.text), ensure_ascii=False),
                strategy=strategy_name,
                chunk_metadata=json.dumps(metadata, ensure_ascii=False) if metadata else None,
                parent_id=parent_id,
            )
            if metadata.get("kind") == "parent":
                parent_id_map[str(metadata.get("parent_key"))] = row_id
            self.session.add(row)
        document.content_hash = content_hash
        document.chunk_count = len(chunk_objs)
        self.session.add(document)
        self.session.commit()
        self.session.refresh(document)
        return document

    async def delete_document(self, document_id: str, user: User) -> bool:
        """软删除：记下删除时间，保留分块、向量和源文件。"""
        doc = self.session.get(Document, document_id)
        if doc is None or not can_write_document(doc, user):
            return False
        doc.deleted_at = datetime.now(UTC)
        self.session.add(doc)
        self.session.commit()
        return True
