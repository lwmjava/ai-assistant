"""RAG-026：上传者范围贯穿检索授权。

``RAG_KB_SCOPE=uploader`` 时，对话检索（只携 tenant_id 的入口）必须按鉴权主体
在取候选**之前**过滤；tenant 模式保持同租户共享，跨租户一律拒绝。
使用隔离 SQLite，避免共享测试库中的历史文档影响 top-k。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core.security import Role
from app.models.rag import Document, DocumentChunk  # noqa: F401 — 注册 RAG 表
from app.models.user import User
from app.rag.access import ReadScope, read_scope_for
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.service import RAGService

_UPLOADER = "uploader"
_TENANT = "tenant"


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
        f"sqlite:///{(tmp_path / 'rag026.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(db)
    return Session(db)


def _rag(session: Session, tenant: str, reader: User | None = None) -> RAGService:
    return RAGService(
        session, tenant, embedding_provider=MockEmbeddingProvider(dim=64), reader=reader
    )


# ── 有效读范围值对象 ──────────────────────────────
def test_read_scope_tenant_mode_has_no_uploader_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT)
    scope = read_scope_for(_user("u1", "t1"))
    assert scope is not None
    assert scope.tenant_id == "t1"
    assert scope.uploader_id is None


def test_read_scope_uploader_mode_narrows_to_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _UPLOADER)
    scope = read_scope_for(_user("u1", "t1"))
    assert scope is not None
    assert scope.uploader_id == "u1"


def test_read_scope_uploader_mode_keeps_system_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _UPLOADER)
    scope = read_scope_for(_user("sa", "t1", Role.SYSTEM_ADMIN))
    assert scope is not None
    assert scope.uploader_id is None


def test_read_scope_without_subject_is_none() -> None:
    assert read_scope_for(None) is None


def test_read_scope_rejects_cross_tenant_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    """主体租户必须等于检索租户，否则范围无效。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _UPLOADER)
    with pytest.raises(ValueError):
        ReadScope(tenant_id="t-other", uploader_id="u1").assert_subject_tenant("t1")


# ── 检索行为 ──────────────────────────────────────
async def test_uploader_mode_peer_cannot_retrieve_owners_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _UPLOADER)
    tenant = f"rag026-u-{uuid4().hex[:8]}"
    marker = f"RAG026OWNER{uuid4().hex[:10]}"
    owner = _user("rag026-owner", tenant)
    peer = _user("rag026-peer", tenant)
    session = _isolated_session(tmp_path)
    try:
        rag_owner = _rag(session, tenant, owner)
        await rag_owner.ingest_text(
            f"{marker} 上传者私有正文，同租户其他成员不应检索到。",
            title="上传者私有",
            source="rag026-owner-doc",
            user_id=owner.id,
        )
        peer_hits = await _rag(session, tenant, peer).search(marker, top_k=5)
        assert all(hit.document_id != _doc_id(session, tenant, "rag026-owner-doc") for hit in peer_hits)
        assert peer_hits == []

        owner_hits = await _rag(session, tenant, owner).search(marker, top_k=5)
        assert owner_hits
    finally:
        session.close()


async def test_uploader_mode_filters_before_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """过滤发生在排序前：更相似的他人文档不能占用本主体的 top-k 预算。

    若实现是先召回再丢弃，top_k=1 时 peer 会因他人文档占位而拿到空结果；
    检索前过滤则应拿到自己那份较不相似的文档。
    """
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _UPLOADER)
    tenant = f"rag026-rank-{uuid4().hex[:8]}"
    marker = f"RAG026RANK{uuid4().hex[:10]}"
    owner = _user("rag026-rank-owner", tenant)
    peer = _user("rag026-rank-peer", tenant)
    session = _isolated_session(tmp_path)
    try:
        rag_owner = _rag(session, tenant, owner)
        await rag_owner.ingest_text(
            f"{marker} {marker} {marker} 他人文档，词面重复度更高。",
            title="他人文档",
            source="rag026-rank-owner",
            user_id=owner.id,
        )
        rag_peer = _rag(session, tenant, peer)
        await rag_peer.ingest_text(
            f"自己的文档，只出现一次关键词 {marker}。",
            title="自己文档",
            source="rag026-rank-peer",
            user_id=peer.id,
        )
        hits = await rag_peer.search(marker, top_k=1)
        assert len(hits) == 1
        assert hits[0].source is not None and "rag026-rank-peer" in hits[0].source
    finally:
        session.close()


async def test_tenant_mode_still_shares_within_tenant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT)
    tenant = f"rag026-t-{uuid4().hex[:8]}"
    marker = f"RAG026SHARE{uuid4().hex[:10]}"
    owner = _user("rag026-share-owner", tenant)
    peer = _user("rag026-share-peer", tenant)
    session = _isolated_session(tmp_path)
    try:
        rag = _rag(session, tenant, owner)
        await rag.ingest_text(
            f"{marker} 同租户共享正文。", title="共享", source="rag026-share", user_id=owner.id
        )
        hits = await _rag(session, tenant, peer).search(marker, top_k=5)
        assert hits
    finally:
        session.close()


async def test_cross_tenant_retrieval_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跨租户主体按自己的租户检索，看不到他人租户的文档。"""
    for scope_mode in (_TENANT, _UPLOADER):
        monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", scope_mode)
        tenant = f"rag026-x-{scope_mode}-{uuid4().hex[:8]}"
        marker = f"RAG026CROSS{uuid4().hex[:10]}"
        owner = _user("rag026-x-owner", tenant)
        foreign = _user("rag026-x-foreign", f"{tenant}-other")
        session = _isolated_session(tmp_path)
        try:
            rag = _rag(session, tenant, owner)
            await rag.ingest_text(
                f"{marker} 跨租户不可见正文。",
                title="跨租户",
                source=f"rag026-x-{scope_mode}",
                user_id=owner.id,
            )
            # 主体只按自己的租户检索：既拿不到他人内容，也不会被误放行。
            hits = await _rag(session, foreign.tenant_id, foreign).search(marker, top_k=5)
            assert hits == [], f"{scope_mode} 模式下跨租户检索必须为空"
        finally:
            session.close()


def test_mismatched_subject_tenant_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务租户与主体租户不一致时拒绝构造，避免范围被跨租户复用。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _UPLOADER)
    from sqlmodel import Session as _Session  # noqa: N812 — 仅用于类型占位

    session = _Session(create_engine("sqlite:///:memory:"))
    with pytest.raises(ValueError):
        RAGService(session, "tenant-a", reader=_user("u1", "tenant-b"))
    session.close()


def _doc_id(session: Session, tenant: str, source: str) -> str:
    from sqlmodel import select

    doc = session.exec(
        select(Document).where(Document.tenant_id == tenant, Document.source == source)
    ).first()
    assert doc is not None
    return doc.id


# ── HTTP 检索面（ADR-0001 §10 要求覆盖「知识库搜索」）────────────
async def test_uploader_mode_http_search_hides_peer_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uploader 模式下，同租户他人不能通过 POST /api/rag/search 命中私有文档。"""
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.core.database import init_db
    from app.main import app

    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _UPLOADER)
    # HTTP 端点自建 RAGService，需与摄取侧使用同一个确定性嵌入提供者。
    monkeypatch.setattr(
        "app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider(dim=64)
    )
    init_db()
    tenant = f"rag026-http-{uuid4().hex[:8]}"
    marker = f"RAG026HTTP{uuid4().hex[:10]}"

    def _as(user: User):
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    owner = _user("rag026-http-owner", tenant)
    peer = _user("rag026-http-peer", tenant)
    from sqlmodel import Session as _Session

    from app.core.database import engine

    with _Session(engine) as session:
        rag = RAGService(
            session, tenant, embedding_provider=MockEmbeddingProvider(dim=64), reader=owner
        )
        doc = await rag.ingest_text(
            f"{marker} HTTP 检索面私有正文。",
            title="HTTP 私有",
            source=f"rag026-http-{marker}",
            user_id=owner.id,
        )
        doc_id = doc.id

    try:
        with _as(owner) as client:
            resp = client.post("/api/rag/search", json={"query": marker, "top_k": 5})
            assert resp.status_code == 200
            owner_ids = {item["document_id"] for item in resp.json()}
        assert doc_id in owner_ids, "上传者本人应能检索到自己的文档"

        with _as(peer) as client:
            resp = client.post("/api/rag/search", json={"query": marker, "top_k": 5})
            assert resp.status_code == 200
            peer_ids = {item["document_id"] for item in resp.json()}
        assert doc_id not in peer_ids, "uploader 模式下 HTTP 检索面不得返回他人文档"
    finally:
        app.dependency_overrides.clear()


async def test_tenant_mode_http_search_still_shares(monkeypatch: pytest.MonkeyPatch) -> None:
    """tenant 模式下 HTTP 检索面仍保持同租户共享。"""
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.core.database import init_db
    from app.main import app

    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT)
    monkeypatch.setattr(
        "app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider(dim=64)
    )
    init_db()
    tenant = f"rag026-http2-{uuid4().hex[:8]}"
    marker = f"RAG026SHAREHTTP{uuid4().hex[:10]}"

    def _as(user: User):
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    owner = _user("rag026-h2-owner", tenant)
    peer = _user("rag026-h2-peer", tenant)
    from sqlmodel import Session as _Session

    from app.core.database import engine

    with _Session(engine) as session:
        rag = RAGService(
            session, tenant, embedding_provider=MockEmbeddingProvider(dim=64), reader=owner
        )
        doc = await rag.ingest_text(
            f"{marker} HTTP 共享正文。",
            title="HTTP 共享",
            source=f"rag026-h2-{marker}",
            user_id=owner.id,
        )
        doc_id = doc.id

    try:
        with _as(peer) as client:
            resp = client.post("/api/rag/search", json={"query": marker, "top_k": 5})
            assert resp.status_code == 200
            assert doc_id in {item["document_id"] for item in resp.json()}
    finally:
        app.dependency_overrides.clear()
