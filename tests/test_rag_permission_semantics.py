"""ADR-0001 To-Be 表征：读路径租户共享当前版本。

默认 ``RAG_KB_SCOPE=tenant``。``uploader`` 回滚路径见 ``tests/test_rag_access.py``。
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.core.database import engine, init_db
from app.core.security import Role
from app.models.user import User
from app.rag.service import RAGService


@pytest.fixture()
def session() -> Session:
    init_db()
    with Session(engine) as s:
        yield s


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


async def test_same_tenant_list_detail_and_search_share_current_documents(
    session: Session,
) -> None:
    tenant = "perm-tenant-shared"
    owner = _user("perm-user-a", tenant)
    peer = _user("perm-user-b", tenant)
    rag = RAGService(session, tenant)

    doc = await rag.ingest_text(
        "权限表征文档：同租户成员应能在列表、详情和检索中看到当前版本。",
        title="权限表征",
        source="perm-char",
        user_id=owner.id,
    )

    listed_owner = {item.id for item in rag.list_documents(owner)}
    listed_peer = {item.id for item in rag.list_documents(peer)}
    assert doc.id in listed_owner
    assert doc.id in listed_peer
    assert rag.get_document(doc.id, peer) is not None

    hits = await rag.search("权限表征文档", top_k=5)
    assert any(hit.document_id == doc.id for hit in hits)


async def test_cross_tenant_search_does_not_return_foreign_documents(
    session: Session,
) -> None:
    rag_a = RAGService(session, "perm-tenant-a")
    rag_b = RAGService(session, "perm-tenant-b")
    doc = await rag_a.ingest_text(
        "跨租户隔离表征：这段文字只属于 tenant-a。",
        title="跨租户表征",
        source="perm-cross",
        user_id="perm-cross-owner",
    )

    hits = await rag_b.search("跨租户隔离表征", top_k=5)
    assert all(hit.document_id != doc.id for hit in hits)
    foreign = _user("perm-foreign", "perm-tenant-b")
    assert rag_a.get_document(doc.id, foreign) is None
    assert doc.id not in {item.id for item in rag_b.list_documents(foreign)}
