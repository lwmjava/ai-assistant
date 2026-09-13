"""表征当前知识库权限语义（As-Is）。

ADR-0001 已批准 To-Be：读路径租户共享当前版本。实现在 RAG-006。
在过滤代码改掉之前，本文件继续锁定现状：
- 列表/详情：非管理员只看到自己上传的当前版本；
- 检索：同租户当前版本都可能命中。

RAG-006 改 `list_documents` / `_can_access` 时必须同步改这些期望。
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


async def test_same_tenant_list_is_uploader_scoped_but_search_is_tenant_scoped(
    session: Session,
) -> None:
    tenant = "perm-tenant-shared"
    owner = _user("perm-user-a", tenant)
    peer = _user("perm-user-b", tenant)
    rag = RAGService(session, tenant)

    doc = await rag.ingest_text(
        "权限表征文档：只有上传者应出现在普通用户的文档列表中。",
        title="权限表征",
        source="perm-char",
        user_id=owner.id,
    )

    listed_owner = {item.id for item in rag.list_documents(owner)}
    listed_peer = {item.id for item in rag.list_documents(peer)}
    assert doc.id in listed_owner
    assert doc.id not in listed_peer
    assert rag.get_document(doc.id, peer) is None

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
