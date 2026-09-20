"""ADR-0001 To-Be 表征：读路径租户共享当前版本。

默认 ``RAG_KB_SCOPE=tenant``。``uploader`` 回滚路径见 ``tests/test_rag_access.py``。
使用隔离 SQLite，避免共享测试库中的历史文档挤掉 top-k。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlmodel import Session, SQLModel, create_engine

from app.core.security import Role
from app.models.rag import Document, DocumentChunk  # noqa: F401 — 注册 RAG 表
from app.models.user import User
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.service import RAGService


def _user(user_id: str, tenant_id: str, role: Role = Role.MEMBER) -> User:
    return User(
        id=user_id,
        tenant_id=tenant_id,
        username=user_id,
        hashed_password="",
        role=role.value,
        token_version=0,
        is_active=True,
    )


def _isolated_session(tmp_path: Path) -> Session:
    db = create_engine(
        f"sqlite:///{(tmp_path / 'perm.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(db)
    return Session(db)


async def test_same_tenant_list_detail_and_search_share_current_documents(
    tmp_path: Path,
) -> None:
    tenant = f"perm-tenant-shared-{uuid4().hex[:8]}"
    marker = f"PERM-SHARE-{uuid4().hex[:12]}"
    owner = _user("perm-user-a", tenant)
    peer = _user("perm-user-b", tenant)
    session = _isolated_session(tmp_path)
    try:
        rag = RAGService(session, tenant, embedding_provider=MockEmbeddingProvider(dim=64))
        doc = await rag.ingest_text(
            f"权限表征文档 {marker}：同租户成员应能在列表、详情和检索中看到当前版本。",
            title="权限表征",
            source="perm-char",
            user_id=owner.id,
        )

        listed_owner = {item.id for item in rag.list_documents(owner)}
        listed_peer = {item.id for item in rag.list_documents(peer)}
        assert doc.id in listed_owner
        assert doc.id in listed_peer
        assert rag.get_document(doc.id, peer) is not None

        hits = await rag.search(marker, top_k=5)
        assert any(hit.document_id == doc.id for hit in hits)
    finally:
        session.close()


async def test_cross_tenant_search_does_not_return_foreign_documents(tmp_path: Path) -> None:
    marker = f"PERM-CROSS-{uuid4().hex[:12]}"
    session = _isolated_session(tmp_path)
    embedding = MockEmbeddingProvider(dim=64)
    try:
        rag_a = RAGService(session, "perm-tenant-a", embedding_provider=embedding)
        rag_b = RAGService(session, "perm-tenant-b", embedding_provider=embedding)
        doc = await rag_a.ingest_text(
            f"跨租户隔离表征 {marker}：这段文字只属于 tenant-a。",
            title="跨租户表征",
            source="perm-cross",
            user_id="perm-cross-owner",
        )

        hits = await rag_b.search(marker, top_k=5)
        assert all(hit.document_id != doc.id for hit in hits)
        foreign = _user("perm-foreign", "perm-tenant-b")
        assert rag_a.get_document(doc.id, foreign) is None
        assert doc.id not in {item.id for item in rag_b.list_documents(foreign)}
    finally:
        session.close()
