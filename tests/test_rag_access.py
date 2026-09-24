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
    kept = session.get(Document, doc.id)
    assert kept is not None and kept.deleted_at is not None
    assert doc.id not in {item.id for item in rag.list_documents(owner)}
    assert rag.get_document(doc.id, owner) is None
    assert doc.id in {item.id for item in rag.list_documents(admin, include_deleted=True)}
    assert doc.id not in {item.id for item in rag.list_documents(peer, include_deleted=True)}
    hits = await rag.search("KB-03", top_k=5)
    assert all(hit.document_id != doc.id for hit in hits)
    assert await rag.delete_document(doc.id, admin) is False


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


async def test_purge_waits_for_retention_then_removes_and_audits(session: Session, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from sqlmodel import select

    from app.audit.models import AuditLog
    from app.rag.retention import RETENTION_DAYS, purge_expired_documents

    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", "tenant")
    tenant = "kb-retention"
    owner = _user("kb-ret-owner", tenant)
    rag = RAGService(session, tenant)
    recent = await rag.ingest_text("保留期内 KEEP-RECENT。", "近期", "recent", owner.id)
    expired = await rag.ingest_text("已过保留期 PURGE-OLD。", "过期", "old", owner.id)
    assert await rag.delete_document(recent.id, owner) is True
    assert await rag.delete_document(expired.id, owner) is True
    now = datetime.now(UTC)
    expired_row = session.get(Document, expired.id)
    assert expired_row is not None
    expired_row.deleted_at = now - timedelta(days=RETENTION_DAYS + 1)
    session.add(expired_row)
    session.commit()

    removed = await purge_expired_documents(session, now=now)
    assert removed == 1
    assert session.get(Document, recent.id) is not None
    assert session.get(Document, expired.id) is None
    logs = session.exec(select(AuditLog).where(AuditLog.resource_id == expired.id)).all()
    assert any('"action": "purge"' in (log.details or "") for log in logs)


async def test_publish_historical_replaces_the_previous_current(session: Session, monkeypatch):
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", "tenant")
    tenant = "kb-publish"
    owner = _user("kb-pub-owner", tenant)
    admin = _user("kb-pub-admin", tenant, Role.TENANT_ADMIN)
    rag = RAGService(session, tenant)
    current = await rag.ingest_text("当前版 PUBLISH-OLD。", "当前", "cur", owner.id)
    historical = await rag.ingest_text(
        "历史版 PUBLISH-NEW。",
        "历史",
        "hist",
        owner.id,
        version_group_id=current.version_group_id,
        is_current=False,
    )
    published = rag.publish_document(historical.id, admin)
    assert published is not None
    assert published.id == historical.id
    session.refresh(current)
    assert published.is_current is True
    assert published.version_state == "published"
    assert current.is_current is False
    assert current.version_state == "replaced"
    currents = [
        row
        for row in rag.list_documents(admin)
        if row.version_group_id == current.version_group_id and row.is_current
    ]
    assert [row.id for row in currents] == [historical.id]
    archived = rag.archive_document(historical.id, admin)
    assert archived is not None and archived.version_state == "archived"
    assert rag.publish_document(historical.id, admin) is None


def test_cross_tenant_delete_requires_confirmation(session: Session):
    from app.rag.confirmations import consume_confirmation, create_confirmation

    doc = Document(
        tenant_id="t-doc",
        user_id="owner",
        title="跨租户",
        is_current=True,
        version_state="published",
    )
    session.add(doc)
    session.commit()
    admin = _user("sys-confirm", "t-platform", Role.SYSTEM_ADMIN)
    with pytest.raises(ValueError):
        consume_confirmation(session, admin, doc, "delete", None)
    row = create_confirmation(session, admin, doc, "delete")
    consume_confirmation(session, admin, doc, "delete", row.id)
    session.commit()
    session.refresh(row)
    assert row.consumed_at is not None
    with pytest.raises(ValueError):
        consume_confirmation(session, admin, doc, "delete", row.id)


def test_member_state_filter_is_ignored(session: Session, monkeypatch):
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", "tenant")
    tenant = "kb-filter"
    owner = _user("kb-filter-owner", tenant)
    doc = Document(
        tenant_id=tenant,
        user_id=owner.id,
        title="草稿",
        is_current=False,
        version_state="draft",
    )
    session.add(doc)
    session.commit()
    rag = RAGService(session, tenant)
    assert doc.id not in {item.id for item in rag.list_documents(owner, version_state="draft")}


def test_uploader_scope_hides_peer_list(monkeypatch):
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", "uploader")
    doc = Document(tenant_id="t-u", user_id="owner", title="x", is_current=True)
    peer = _user("peer", "t-u")
    assert can_read_document(doc, peer) is False
    assert can_read_import("owner", "t-u", peer) is False
