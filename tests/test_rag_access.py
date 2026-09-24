"""知识库读写权限与导入可见性。"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.core.database import engine, init_db
from app.core.security import Role
from app.models.rag import Document
from app.models.user import User
from app.rag.access import can_read_document, can_read_import, can_write_document
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


async def test_kb01_peer_lists_same_tenant_current_document(session: Session, monkeypatch):
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", "tenant")
    tenant = "kb-tenant-list"
    owner = _user("kb-owner", tenant)
    peer = _user("kb-peer", tenant)
    rag = RAGService(session, tenant)
    doc = await rag.ingest_text("KB-01 同租户共享正文。", "KB-01", "kb01", owner.id)
    assert doc.id in {item.id for item in rag.list_documents(owner)}
    assert doc.id not in {item.id for item in rag.list_documents(peer)}
    assert rag.get_document(doc.id, peer) is None
    assert can_read_document(doc, peer) is True


async def test_kb03_member_cannot_delete_peer_admin_can(session: Session, monkeypatch):
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", "tenant")
    tenant = "kb-tenant-write"
    owner = _user("kb-w-owner", tenant)
    peer = _user("kb-w-peer", tenant)
    admin = _user("kb-w-admin", tenant, Role.TENANT_ADMIN)
    rag = RAGService(session, tenant)
    doc = await rag.ingest_text("KB-03 写权限。", "KB-03", "kb03", owner.id)
    assert await rag.delete_document(doc.id, peer) is False
    assert session.get(Document, doc.id) is not None
    assert await rag.delete_document(doc.id, admin) is True
    assert session.get(Document, doc.id) is None


async def test_stale_version_not_in_list_or_detail(session: Session, monkeypatch):
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", "tenant")
    tenant = "kb-stale"
    owner = _user("kb-stale-owner", tenant)
    rag = RAGService(session, tenant)
    doc = await rag.ingest_text("旧版本不应出现。", "旧版", "stale", owner.id)
    doc.is_current = False
    session.add(doc)
    session.commit()
    admin = _user("kb-stale-admin", tenant, Role.TENANT_ADMIN)
    assert doc.id not in {item.id for item in rag.list_documents(owner)}
    assert rag.get_document(doc.id, owner) is None
    assert can_write_document(doc, owner) is False
    assert doc.id in {item.id for item in rag.list_documents(admin)}
    assert rag.get_document(doc.id, admin) is not None
    assert can_write_document(doc, admin) is True
    assert can_read_document(doc, owner) is False


def test_cross_tenant_read_and_write_denied():
    doc = Document(tenant_id="t-a", user_id="u-a", title="x", is_current=True)
    other = _user("u-b", "t-b")
    admin = _user("sys", "t-platform", Role.SYSTEM_ADMIN)
    assert can_read_document(doc, other) is False
    assert can_write_document(doc, other) is False
    assert can_write_document(doc, admin) is True
    assert can_read_import("u-a", "t-a", other) is False


def test_uploader_scope_hides_peer_list(monkeypatch):
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", "uploader")
    doc = Document(tenant_id="t-u", user_id="owner", title="x", is_current=True)
    peer = _user("peer", "t-u")
    assert can_read_document(doc, peer) is False
    assert can_read_import("owner", "t-u", peer) is False
