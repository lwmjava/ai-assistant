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

from sqlmodel import Session, select

from app.core.config import settings
from app.models.rag import Document, DocumentChunk
from app.models.user import User
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.embeddings.mock import tokenize
from app.rag.ingestion import split_text
from app.rag.retriever import HybridRetriever
from app.rag.vectorstore.base import ChunkResult, VectorStore
from app.rag.vectorstore.factory import get_vector_store

logger = logging.getLogger(__name__)

# 单次批量嵌入的最大文本数，避免超长文档一次性压垮嵌入接口。
_EMBED_BATCH = 32


class RAGService:
    """检索增强生成服务（会话级，绑定一个数据库会话与租户）。"""

    def __init__(
        self,
        session: Session,
        tenant_id: str,
        embedding_provider: EmbeddingProvider | None = None,
        vector_store: VectorStore | None = None,
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self._embedding = embedding_provider or get_embedding_provider()
        self._vector_store = vector_store or get_vector_store(session)

    # ── 摄取 ────────────────────────────────────────
    async def ingest_text(
        self, text: str, title: str, source: str | None, user_id: str
    ) -> Document:
        """摄取一段文本：分块、嵌入、落库，返回文档记录。"""
        chunks = split_text(
            text, settings.RAG_CHUNK_SIZE, settings.RAG_CHUNK_OVERLAP
        )
        if not chunks:
            raise ValueError("文本为空或无法切分为任何分块")

        document = Document(
            tenant_id=self.tenant_id,
            user_id=user_id,
            title=title,
            source=source,
            chunk_count=0,
        )
        self.session.add(document)
        self.session.commit()
        self.session.refresh(document)

        chunk_rows: list[DocumentChunk] = []
        for index, content in enumerate(chunks):
            row = DocumentChunk(
                tenant_id=self.tenant_id,
                document_id=document.id,
                chunk_index=index,
                content=content,
                source=source,
                tokens=json.dumps(tokenize(content), ensure_ascii=False),
            )
            chunk_rows.append(row)
            self.session.add(row)

        # 批量嵌入并回写向量。
        await self._embed_and_store(chunk_rows)
        document.chunk_count = len(chunk_rows)
        self.session.add(document)
        self.session.commit()
        self.session.refresh(document)
        return document

    async def ingest_file(self, path: str, title: str | None, user_id: str) -> Document:
        """摄取本地文本文件（支持 .txt / .md）。"""
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".txt", ".md"):
            raise ValueError("仅支持 .txt / .md 文本文件摄取")
        with open(path, "r", encoding="utf-8") as fh:
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
    async def search(self, query: str, top_k: int | None = None) -> list[ChunkResult]:
        """对查询做混合检索，返回融合排序后的分块。"""
        top_k = top_k or settings.RAG_TOP_K
        embedding = (await self._embedding.embed([query]))[0]
        results = await self._vector_store.hybrid_search(
            embedding, tokenize(query), self.tenant_id, top_k, settings.RAG_HYBRID_RRF_K
        )
        return results

    def make_retriever(self, top_k: int | None = None) -> HybridRetriever:
        """生成可注入 Agent 管线的混合检索器。"""
        return HybridRetriever(
            self._embedding,
            self._vector_store,
            self.tenant_id,
            top_k or settings.RAG_TOP_K,
            settings.RAG_HYBRID_RRF_K,
        )

    # ── 文档管理 ────────────────────────────────────
    def _can_access(self, doc: Document, user: User) -> bool:
        if user.role_enum.value == "system_admin":
            return doc.tenant_id == user.tenant_id
        return doc.user_id == user.id and doc.tenant_id == user.tenant_id

    def list_documents(self, user: User) -> list[Document]:
        """列出当前用户可见的文档（系统管理员可见同租户全部）。"""
        stmt = select(Document).where(Document.tenant_id == user.tenant_id)
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

    def delete_document(self, document_id: str, user: User) -> bool:
        """删除文档（级联删除分块与主库记录，并清理向量索引）。"""
        doc = self.get_document(document_id, user)
        if doc is None:
            return False
        self.session.delete(doc)
        self.session.commit()
        return True
