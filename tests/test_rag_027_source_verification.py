"""RAG-027：授权引用原文只读核验（ADR-0007）。

覆盖 tenant / uploader 两种检索范围下的块级核验：有权命中块可读正文与定位
信息，跨租户、旧版、软删、错误关联、伪造来源一律失败关闭；父块独立鉴权；
核验者的删改 / 重解析 / 整文件下载权限与改动前一致。

``GET /api/rag/chunks/{chunk_id}/evidence`` 是新的只读入口，本文件同时守住
「不返回整章 / 整文件」的边界：响应字段被逐个钉住。

「ADR-0007 §4 不缓存 / 每次重新鉴权」与「§5 时效过滤」在本文件里都有
allow → deny 两个方向的反例：先成功后失败的顺序同样被钉住，只测「本来就拒绝」
的方向抓不到「复用上次授权结论」这类实现。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.api.deps import get_current_user
from app.core.database import engine, init_db
from app.core.security import Role
from app.main import app
from app.models.rag import Document, DocumentChunk, ImportJob
from app.models.user import User
from app.rag.access import can_control_document, can_write_document
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.evidence import EVIDENCE_DENIED_MESSAGE, EvidenceDeniedError, load_chunk_evidence
from app.rag.service import RAGService

_TENANT_SCOPE = "tenant"
_UPLOADER_SCOPE = "uploader"

# 响应字段被钉死：任何整文档正文 / 源文件 / ACL 字段都会让这条断言失败。
_EVIDENCE_FIELDS = {
    "chunk_id",
    "document_id",
    "document_title",
    "version_state",
    "chunk_index",
    "content",
    "source",
    "page",
    "section",
    "source_start",
    "source_end",
    "parent",
}
# 四角色在两种检索范围下「是否能在检索面看到该块」的裁决取值（M-03）。
# tenant：同租户共享；uploader：仅本人可见，TENANT_ADMIN 不豁免，
# SYSTEM_ADMIN 与 RAG-026 已批准的 read_scope_for 行为一致（同租户可见）。
_EXPECTED_FACE: dict[str, dict[str, bool]] = {
    "tenant": {"owner": True, "peer": True, "tenant_admin": True, "system_admin": True},
    "uploader": {"owner": True, "peer": False, "tenant_admin": False, "system_admin": True},
}

_LOCATOR_FIELDS = {"chunk_id", "chunk_index", "page", "section", "source_start", "source_end", "content"}


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


@contextmanager
def _as(user: User) -> Iterator[TestClient]:
    """以指定主体调用 HTTP 入口。"""
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _seed_document(
    session: Session,
    *,
    tenant_id: str,
    owner_id: str,
    title: str,
    is_current: bool = True,
    version_state: str = "published",
    deleted_at: datetime | None = None,
    source: str | None = None,
) -> Document:
    doc = Document(
        tenant_id=tenant_id,
        user_id=owner_id,
        title=title,
        source=source,
        is_current=is_current,
        version_state=version_state,
        deleted_at=deleted_at,
        chunk_count=0,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def _seed_chunk(
    session: Session,
    doc: Document,
    *,
    content: str,
    chunk_index: int = 0,
    parent_id: str | None = None,
    tenant_id: str | None = None,
    metadata: dict | None = None,
) -> DocumentChunk:
    chunk = DocumentChunk(
        # 缺省与文档同租户；显式传入用于制造「块与文档租户不一致」的伪造数据。
        tenant_id=tenant_id if tenant_id is not None else doc.tenant_id,
        document_id=doc.id,
        chunk_index=chunk_index,
        content=content,
        source=doc.source,
        parent_id=parent_id,
        chunk_metadata=json.dumps(metadata or {}, ensure_ascii=False),
    )
    session.add(chunk)
    session.commit()
    session.refresh(chunk)
    return chunk


def _rich_metadata() -> dict:
    return {"page": 3, "section_path": ["第二章", "2.1 计费"], "source_start": 40, "source_end": 96}


def _evidence(chunk_id: str, **params: str) -> str:
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"/api/rag/chunks/{chunk_id}/evidence" + (f"?{query}" if query else "")


def _denied(resp) -> None:
    """失败关闭的三条硬约束：404、固定文案、响应里没有任何正文。"""
    assert resp.status_code == 404
    assert resp.json() == {"detail": EVIDENCE_DENIED_MESSAGE}


# ── tenant 模式：A 上传、B 核验 ────────────────────────
async def test_tenant_mode_peer_verifies_authorized_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """B 能检索到的命中块，B 就能核验其原文与定位信息。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider(dim=64))
    init_db()
    tenant = f"rag027-tenant-{uuid4().hex[:8]}"
    marker = f"RAG027HIT{uuid4().hex[:10]}"
    owner = _user("rag027-a", tenant)
    peer = _user("rag027-b", tenant)

    with Session(engine) as session:
        rag = RAGService(
            session, tenant, embedding_provider=MockEmbeddingProvider(dim=64), reader=owner
        )
        doc = await rag.ingest_text(
            f"{marker} 同租户共享正文，用于核验命中块原文。",
            title="共享手册",
            source="rag027-shared",
            user_id=owner.id,
        )
        # B 的检索面确实能命中，才谈得上「核验有权命中块」。
        hits = await RAGService(
            session, tenant, embedding_provider=MockEmbeddingProvider(dim=64), reader=peer
        ).search(marker, top_k=5)
        assert hits, "tenant 模式下 B 必须能检索到 A 上传的文档"
        hit_id = hits[0].id
        hit_content = hits[0].content
        document_id = doc.id

    with _as(peer) as client:
        resp = client.get(_evidence(hit_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == hit_content
    assert body["document_id"] == document_id
    assert body["document_title"] == "共享手册"
    assert body["version_state"] == "published"
    assert body["chunk_index"] == 0
    # 定位信息字段齐全：有则给，没有则显式为 null。
    assert set(body) == _EVIDENCE_FIELDS
    assert body["page"] is None or isinstance(body["page"], int)
    assert body["source_start"] is None or isinstance(body["source_start"], int)


def test_tenant_mode_returns_locator_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """页码 / 段落 / 源范围随块元数据返回，不编造没有的部分。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-loc-{uuid4().hex[:8]}"
    owner = _user("rag027-loc-a", tenant)
    peer = _user("rag027-loc-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="手册", source="rag027-loc")
        chunk_id = _seed_chunk(session, doc, content="核验正文", metadata=_rich_metadata()).id

    with _as(peer) as client:
        resp = client.get(_evidence(chunk_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 3
    assert body["section"] == "第二章 / 2.1 计费"
    assert body["source_start"] == 40
    assert body["source_end"] == 96
    assert body["chunk_index"] == 0
    assert body["content"] == "核验正文"


def test_tenant_mode_missing_metadata_is_explicit_null(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有页码 / 段落 / 源范围时显式返回 null，不推断。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-null-{uuid4().hex[:8]}"
    owner = _user("rag027-null-a", tenant)
    peer = _user("rag027-null-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="无定位文档")
        chunk_id = _seed_chunk(session, doc, content="无定位正文").id

    with _as(peer) as client:
        body = client.get(_evidence(chunk_id)).json()
    assert body["page"] is None
    assert body["section"] is None
    assert body["source_start"] is None
    assert body["source_end"] is None


def test_soft_deleted_document_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-del-{uuid4().hex[:8]}"
    owner = _user("rag027-del-a", tenant)
    peer = _user("rag027-del-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(
            session,
            tenant_id=tenant,
            owner_id=owner.id,
            title="已删除",
            deleted_at=datetime.now(UTC),
        )
        chunk_id = _seed_chunk(session, doc, content="软删正文不应可读").id

    with _as(peer) as client:
        _denied(client.get(_evidence(chunk_id)))
    # 上传者本人同样不通过：软删是硬条件，不是归属问题。
    with _as(owner) as client:
        _denied(client.get(_evidence(chunk_id)))


def test_replaced_version_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧版本不通过普通引用核验读取，也不自动指向新版。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-old-{uuid4().hex[:8]}"
    owner = _user("rag027-old-a", tenant)
    peer = _user("rag027-old-b", tenant)
    with Session(engine) as session:
        old = _seed_document(
            session,
            tenant_id=tenant,
            owner_id=owner.id,
            title="旧版手册",
            is_current=False,
            version_state="replaced",
        )
        old_chunk_id = _seed_chunk(session, old, content="旧版正文").id
        new = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="新版手册")
        new_chunk_id = _seed_chunk(session, new, content="新版正文").id
        new_doc_id = new.id

    with _as(peer) as client:
        _denied(client.get(_evidence(old_chunk_id)))
        # 旧引用失效时明确不可用，不把旧引用重定向到新版。
        resp = client.get(_evidence(new_chunk_id))
        assert resp.status_code == 200
        assert resp.json()["content"] == "新版正文"
        assert resp.json()["document_id"] == new_doc_id

    # 服务层同样拒绝：不经过 HTTP 也不能拿到旧版正文。
    with Session(engine) as session:
        with pytest.raises(EvidenceDeniedError):
            load_chunk_evidence(session, old_chunk_id, peer)


def test_unpublished_draft_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """未发布草稿不通过普通引用核验读取（ADR-0007 §5）。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-draft-{uuid4().hex[:8]}"
    owner = _user("rag027-draft-a", tenant)
    peer = _user("rag027-draft-b", tenant)
    with Session(engine) as session:
        draft = _seed_document(
            session,
            tenant_id=tenant,
            owner_id=owner.id,
            title="草稿手册",
            is_current=False,
            version_state="draft",
        )
        draft_chunk_id = _seed_chunk(session, draft, content="草稿正文").id

    with _as(peer) as client:
        _denied(client.get(_evidence(draft_chunk_id)))
    with _as(owner) as client:
        _denied(client.get(_evidence(draft_chunk_id)))


def test_cross_tenant_chunk_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-x-{uuid4().hex[:8]}"
    other = f"{tenant}-other"
    foreign_owner = _user("rag027-x-a", other)
    insider = _user("rag027-x-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=other, owner_id=foreign_owner.id, title="他租户文档")
        chunk_id = _seed_chunk(session, doc, content="他租户正文").id

    with _as(insider) as client:
        _denied(client.get(_evidence(chunk_id)))

    # 连本租户自己拥有的文档 ID 一起传也不放宽：关联不真实就是拒绝。
    with Session(engine) as session:
        mine = _seed_document(session, tenant_id=tenant, owner_id=insider.id, title="我的文档")
        _seed_chunk(session, mine, content="我的正文")
        mine_id = mine.id
    with _as(insider) as client:
        _denied(client.get(_evidence(chunk_id, document_id=mine_id)))


def test_denial_hides_existence_details(monkeypatch: pytest.MonkeyPatch) -> None:
    """不存在的块与无权的块返回完全一致：不泄漏存在性。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-exist-{uuid4().hex[:8]}"
    other = f"{tenant}-other"
    owner = _user("rag027-e-a", other)
    outsider = _user("rag027-e-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=other, owner_id=owner.id, title="他租户文档")
        real_chunk_id = _seed_chunk(session, doc, content="他租户正文").id

    with _as(outsider) as client:
        missing = client.get(_evidence(f"nosuchchunk-{uuid4().hex}"))
        forbidden = client.get(_evidence(real_chunk_id))
    assert missing.status_code == forbidden.status_code == 404
    assert missing.json() == forbidden.json() == {"detail": EVIDENCE_DENIED_MESSAGE}
    assert real_chunk_id not in missing.text and real_chunk_id not in forbidden.text


# ── uploader 模式 ─────────────────────────────────────
def test_uploader_mode_peer_cannot_verify_owners_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _UPLOADER_SCOPE)
    init_db()
    tenant = f"rag027-up-{uuid4().hex[:8]}"
    owner = _user("rag027-up-a", tenant)
    peer = _user("rag027-up-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="A 的文档")
        chunk_id = _seed_chunk(session, doc, content="A 的私有正文").id

    with _as(peer) as client:
        _denied(client.get(_evidence(chunk_id)))
    with _as(owner) as client:
        resp = client.get(_evidence(chunk_id))
    assert resp.status_code == 200
    assert resp.json()["content"] == "A 的私有正文"


def test_uploader_mode_tenant_admin_is_not_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0007 §3：uploader 模式下管理员的核验读路径同样不豁免。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _UPLOADER_SCOPE)
    init_db()
    tenant = f"rag027-admin-{uuid4().hex[:8]}"
    owner = _user("rag027-ad-a", tenant)
    admin = _user("rag027-ad-admin", tenant, Role.TENANT_ADMIN)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="成员文档")
        chunk_id = _seed_chunk(session, doc, content="成员上传的正文").id

    with _as(admin) as client:
        _denied(client.get(_evidence(chunk_id)))

    # 管理员自己上传的内容仍可核验：限制的是他人内容，不是管理员身份。
    with Session(engine) as session:
        own = _seed_document(session, tenant_id=tenant, owner_id=admin.id, title="管理员文档")
        own_chunk_id = _seed_chunk(session, own, content="管理员自己的正文").id
    with _as(admin) as client:
        assert client.get(_evidence(own_chunk_id)).status_code == 200


# ── 父块独立鉴权 ──────────────────────────────────────
def test_parent_defaults_to_locator_without_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """子块有权不等于父块整段可读：默认只给定位信息。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-par-{uuid4().hex[:8]}"
    owner = _user("rag027-par-a", tenant)
    peer = _user("rag027-par-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="父子文档")
        parent_id = _seed_chunk(session, doc, content="父块完整正文", chunk_index=0).id
        child_id = _seed_chunk(
            session, doc, content="子块正文", chunk_index=1, parent_id=parent_id, metadata=_rich_metadata()
        ).id

    with _as(peer) as client:
        resp = client.get(_evidence(child_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["parent"] is not None
    assert set(body["parent"]) == _LOCATOR_FIELDS
    assert body["parent"]["chunk_id"] == parent_id
    assert body["parent"]["chunk_index"] == 0
    assert body["parent"]["content"] is None, "默认不得返回父正文"
    assert "父块完整正文" not in json.dumps(body, ensure_ascii=False)


def test_parent_content_only_when_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式请求父正文时，父块仍要独立通过同文档关系与同一检索授权。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-par2-{uuid4().hex[:8]}"
    owner = _user("rag027-par2-a", tenant)
    peer = _user("rag027-par2-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="父子文档")
        parent_id = _seed_chunk(session, doc, content="父块正文", chunk_index=0).id
        child_id = _seed_chunk(session, doc, content="子块正文", chunk_index=1, parent_id=parent_id).id

    with _as(peer) as client:
        resp = client.get(_evidence(child_id, include_parent_content="true"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["parent"] is not None
    assert body["parent"]["content"] == "父块正文"
    # 即便返回父正文，也只有一个父块，不是整章或整文件。
    assert body["content"] == "子块正文"


def test_parent_in_other_tenant_document_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-parx-{uuid4().hex[:8]}"
    other = f"{tenant}-other"
    owner = _user("rag027-parx-a", tenant)
    foreign = _user("rag027-parx-f", other)
    peer = _user("rag027-parx-b", tenant)
    with Session(engine) as session:
        foreign_doc = _seed_document(session, tenant_id=other, owner_id=foreign.id, title="他租户父文档")
        foreign_parent_id = _seed_chunk(session, foreign_doc, content="他租户父块正文").id
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="本租户文档")
        child_id = _seed_chunk(session, doc, content="子块正文", parent_id=foreign_parent_id).id

    with _as(peer) as client:
        resp = client.get(_evidence(child_id, include_parent_content="true"))
    assert resp.status_code == 200, "子块本身仍可核验"
    assert resp.json()["content"] == "子块正文"
    assert resp.json()["parent"] is None, "跨文档 / 跨租户父块不得返回任何信息"
    assert "他租户父块正文" not in json.dumps(resp.json(), ensure_ascii=False)


def test_parent_owned_by_other_uploader_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """uploader 模式下，父块属他人上传内容时不得返回父正文。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _UPLOADER_SCOPE)
    init_db()
    tenant = f"rag027-paru-{uuid4().hex[:8]}"
    owner = _user("rag027-paru-a", tenant)
    peer = _user("rag027-paru-b", tenant)
    with Session(engine) as session:
        owner_doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="A 的文档")
        foreign_parent_id = _seed_chunk(session, owner_doc, content="A 上传的父块正文").id
        peer_doc = _seed_document(session, tenant_id=tenant, owner_id=peer.id, title="B 的文档")
        child_id = _seed_chunk(session, peer_doc, content="B 的子块正文", parent_id=foreign_parent_id).id

    with _as(peer) as client:
        resp = client.get(_evidence(child_id, include_parent_content="true"))
    assert resp.status_code == 200
    assert resp.json()["content"] == "B 的子块正文"
    assert resp.json()["parent"] is None
    assert "A 上传的父块正文" not in json.dumps(resp.json(), ensure_ascii=False)


def test_parent_of_replaced_version_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """父块挂在已替换版本上：独立鉴权不通过，不返回父正文。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-parr-{uuid4().hex[:8]}"
    owner = _user("rag027-parr-a", tenant)
    peer = _user("rag027-parr-b", tenant)
    with Session(engine) as session:
        old = _seed_document(
            session, tenant_id=tenant, owner_id=owner.id, title="旧版", is_current=False, version_state="replaced"
        )
        old_parent_id = _seed_chunk(session, old, content="旧版父块正文").id
        current = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="当前版")
        child_id = _seed_chunk(session, current, content="当前版子块正文", parent_id=old_parent_id).id

    with _as(peer) as client:
        resp = client.get(_evidence(child_id, include_parent_content="true"))
    assert resp.status_code == 200
    assert resp.json()["parent"] is None
    assert "旧版父块正文" not in json.dumps(resp.json(), ensure_ascii=False)


# ── 错误关联与伪造来源 ────────────────────────────────
def test_claimed_document_mismatch_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """调用方声称的文档与实际关联不一致即拒绝：关联只能从库里查。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-claim-{uuid4().hex[:8]}"
    owner = _user("rag027-claim-a", tenant)
    peer = _user("rag027-claim-b", tenant)
    with Session(engine) as session:
        owner_doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="A 的文档")
        owner_chunk_id = _seed_chunk(session, owner_doc, content="A 的正文").id
        peer_doc = _seed_document(session, tenant_id=tenant, owner_id=peer.id, title="B 的文档")
        _seed_chunk(session, peer_doc, content="B 的正文")
        peer_doc_id = peer_doc.id
        owner_doc_id = owner_doc.id

    # B 对 peer_doc 有读权，但块并不属于它：不得凭传入的 document_id 放行。
    with _as(peer) as client:
        _denied(client.get(_evidence(owner_chunk_id, document_id=peer_doc_id)))
    # 声称正确时仍按真实关联处理（tenant 模式下放行）。
    with _as(peer) as client:
        assert client.get(_evidence(owner_chunk_id, document_id=owner_doc_id)).status_code == 200


def test_chunk_document_tenant_mismatch_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """块的租户与所属文档不一致：数据被拼改，失败关闭。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-tamp-{uuid4().hex[:8]}"
    owner = _user("rag027-tamp-a", tenant)
    peer = _user("rag027-tamp-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="本租户文档")
        # 伪造：块声称属于本租户文档，却带着他租户的 tenant_id。
        chunk_id = _seed_chunk(session, doc, content="被拼改的正文", tenant_id=f"{tenant}-other").id

    with _as(peer) as client:
        _denied(client.get(_evidence(chunk_id)))
    with _as(owner) as client:
        _denied(client.get(_evidence(chunk_id)))


# ── 权限不变：核验不授予删改 / 重解析 / 整文件下载 ─────
def test_verifier_cannot_delete_or_reparse_owners_document(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-ctl-{uuid4().hex[:8]}"
    owner = _user("rag027-ctl-a", tenant)
    peer = _user("rag027-ctl-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="A 的文档")
        _seed_chunk(session, doc, content="A 的正文")
        doc_id = doc.id

    with _as(peer) as client:
        # 删除：权限矩阵不变，成员仍删不掉他人文档。
        assert client.delete(f"/api/rag/documents/{doc_id}").status_code == 404
        # 重解析：同样被拒，且没有留下任务。
        assert client.post(f"/api/rag/documents/{doc_id}/reparse").status_code == 400

    with Session(engine) as session:
        stored = session.get(Document, doc_id)
        assert stored is not None
        assert stored.deleted_at is None
        assert (
            session.exec(select(ImportJob).where(col(ImportJob.reparse_document_id) == doc_id)).first() is None
        )
        # 直接调用控制面判定，确认与改动前一致。
        assert can_write_document(stored, peer) is False
        assert can_control_document(stored, peer) is False
        assert can_write_document(stored, owner) is True


def test_verifier_cannot_download_source_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """核验读路径不扩大整文件下载：下载仍走控制面判定。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-dl-{uuid4().hex[:8]}"
    owner = _user("rag027-dl-a", tenant)
    peer = _user("rag027-dl-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="A 的文档", source="a.txt")
        _seed_chunk(session, doc, content="A 的正文")
        doc_id = doc.id

    with _as(peer) as client:
        assert client.get(f"/api/rag/documents/{doc_id}/download").status_code == 404
        # 有核验入口也不代表能读到控制面详情。
        assert client.get(f"/api/rag/documents/{doc_id}").status_code == 404


def test_control_plane_matrix_unchanged_for_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """管理员的控制面权限按 ADR-0001 矩阵执行，不因新增核验入口变化。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-matrix-{uuid4().hex[:8]}"
    owner = _user("rag027-mx-a", tenant)
    admin = _user("rag027-mx-admin", tenant, Role.TENANT_ADMIN)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="成员文档")
        _seed_chunk(session, doc, content="成员正文")
        doc_id = doc.id

    with Session(engine) as session:
        stored = session.get(Document, doc_id)
        assert stored is not None
        assert can_control_document(stored, admin) is True
        assert can_write_document(stored, admin) is True
        assert can_control_document(stored, owner) is True
    with _as(admin) as client:
        assert client.get(f"/api/rag/documents/{doc_id}").status_code == 200


# ── 返回范围：不返回整章 / 整文件 ─────────────────────
async def test_evidence_never_returns_whole_document(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider(dim=64))
    init_db()
    tenant = f"rag027-scope-{uuid4().hex[:8]}"
    marker = f"RAG027SCOPE{uuid4().hex[:10]}"
    owner = _user("rag027-sc-a", tenant)
    peer = _user("rag027-sc-b", tenant)
    long_text = "。".join(f"{marker} 第 {i} 段用于验证核验入口只返回单个块" for i in range(40))

    with Session(engine) as session:
        rag = RAGService(
            session, tenant, embedding_provider=MockEmbeddingProvider(dim=64), reader=owner
        )
        doc = await rag.ingest_text(long_text, title="长文档", source="rag027-scope", user_id=owner.id)
        chunks = list(
            session.exec(
                select(DocumentChunk)
                .where(col(DocumentChunk.document_id) == doc.id)
                .order_by(col(DocumentChunk.chunk_index).asc())
            ).all()
        )
        first_id = chunks[0].id
        first_content = chunks[0].content
        second_content = chunks[1].content
    assert len(chunks) > 1, "前提：文档确实被切成多个块"

    with _as(peer) as client:
        body = client.get(_evidence(first_id)).json()
    assert set(body) == _EVIDENCE_FIELDS
    assert body["content"] == first_content
    assert len(body["content"]) < len(long_text)
    for forbidden in ("document_text", "text", "original_text", "storage_path", "acl", "content_hash"):
        assert forbidden not in body
    assert second_content not in json.dumps(body, ensure_ascii=False)


# ── M-01：不缓存、每次请求重新鉴权（allow → deny 方向）──
def test_previous_verification_is_not_reused_for_another_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一 chunk_id、同一进程：owner 先 200，换 peer 必须立刻 404。

    只测「peer 先被拒 → owner 通过」抓不到「复用上次授权结论」的实现，
    这条把成功结果放在前面，正是 ADR-0007 §4「缓存文本不能充当授权证明」
    要求的方向。
    """
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _UPLOADER_SCOPE)
    init_db()
    tenant = f"rag027-cache-{uuid4().hex[:8]}"
    owner = _user("rag027-cache-a", tenant)
    peer = _user("rag027-cache-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="A 的文档")
        chunk_id = _seed_chunk(session, doc, content="A 的正文，不得被他人复用").id

    with _as(owner) as client:
        assert client.get(_evidence(chunk_id)).status_code == 200
    # 紧接着的第二次请求换了主体：上一次的成功结论不得被沿用。
    with _as(peer) as client:
        _denied(client.get(_evidence(chunk_id)))
    # 被拒之后 owner 仍可核验：说明不是把块拉黑，而是每次都重新判定。
    with _as(owner) as client:
        assert client.get(_evidence(chunk_id)).status_code == 200


def test_document_state_change_takes_effect_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一主体两次请求之间文档被软删：第二次必须 404。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-state-{uuid4().hex[:8]}"
    owner = _user("rag027-state-a", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="将失效的文档")
        chunk_id = _seed_chunk(session, doc, content="将失效的正文").id
        doc_id = doc.id

    with _as(owner) as client:
        assert client.get(_evidence(chunk_id)).status_code == 200

    with Session(engine) as session:
        stored = session.get(Document, doc_id)
        assert stored is not None
        stored.deleted_at = datetime.now(UTC)
        session.add(stored)
        session.commit()

    with _as(owner) as client:
        _denied(client.get(_evidence(chunk_id)))


def test_scope_tightening_takes_effect_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一主体两次请求之间检索范围由 tenant 收紧为 uploader：第二次必须 404。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-tight-{uuid4().hex[:8]}"
    owner = _user("rag027-tight-a", tenant)
    peer = _user("rag027-tight-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="A 的文档")
        chunk_id = _seed_chunk(session, doc, content="A 的正文").id

    with _as(peer) as client:
        assert client.get(_evidence(chunk_id)).status_code == 200

    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _UPLOADER_SCOPE)
    with _as(peer) as client:
        _denied(client.get(_evidence(chunk_id)))


# ── M-02：时效过滤开关（ADR-0007 §5）──────────────────
@pytest.mark.parametrize("case", ["scheduled", "expired"])
def test_effective_date_filter_blocks_unpublished_and_expired(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """开关打开时，未生效（scheduled）与已过期的版本不通过普通引用核验读取。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    monkeypatch.setattr("app.rag.evidence.settings.RAG_EFFECTIVE_DATE_FILTER", True)
    init_db()
    tenant = f"rag027-eff-{uuid4().hex[:8]}"
    owner = _user("rag027-eff-a", tenant)
    peer = _user("rag027-eff-b", tenant)
    now = datetime.now(UTC)
    with Session(engine) as session:
        future = _seed_document(
            session,
            tenant_id=tenant,
            owner_id=owner.id,
            title="未生效文档",
            version_state="scheduled",
        )
        future.effective_at = now + timedelta(days=1)
        session.add(future)
        session.commit()
        future_chunk_id = _seed_chunk(session, future, content="未生效正文").id

        expired = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="已过期文档")
        expired.expires_at = now - timedelta(days=1)
        session.add(expired)
        session.commit()
        expired_chunk_id = _seed_chunk(session, expired, content="已过期正文").id

    target = future_chunk_id if case == "scheduled" else expired_chunk_id
    with _as(peer) as client:
        _denied(client.get(_evidence(target)))
    # 服务层直连同样拒绝：绕过 HTTP 也拿不到未生效 / 已过期的正文。
    with Session(engine) as session:
        with pytest.raises(EvidenceDeniedError):
            load_chunk_evidence(session, target, peer)


def test_effective_date_filter_still_allows_live_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关打开时落在生效窗口内的当前版本仍可核验（防止有人把开关语义反过来改）。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    monkeypatch.setattr("app.rag.evidence.settings.RAG_EFFECTIVE_DATE_FILTER", True)
    init_db()
    tenant = f"rag027-efflive-{uuid4().hex[:8]}"
    owner = _user("rag027-efflive-a", tenant)
    peer = _user("rag027-efflive-b", tenant)
    with Session(engine) as session:
        live = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="生效中文档")
        live_chunk_id = _seed_chunk(session, live, content="生效中正文").id

    with _as(peer) as client:
        resp = client.get(_evidence(live_chunk_id))
    assert resp.status_code == 200
    assert resp.json()["content"] == "生效中正文"


def test_effective_date_filter_off_keeps_is_current_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关关闭（默认）时只按当前版本判定，避免有人把开关语义反过来改。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    monkeypatch.setattr("app.rag.evidence.settings.RAG_EFFECTIVE_DATE_FILTER", False)
    init_db()
    tenant = f"rag027-effoff-{uuid4().hex[:8]}"
    owner = _user("rag027-effoff-a", tenant)
    peer = _user("rag027-effoff-b", tenant)
    with Session(engine) as session:
        future = _seed_document(
            session, tenant_id=tenant, owner_id=owner.id, title="预告文档", version_state="scheduled"
        )
        future.effective_at = datetime.now(UTC) + timedelta(days=1)
        session.add(future)
        session.commit()
        future_chunk_id = _seed_chunk(session, future, content="预告正文").id
        expired = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="已过期文档")
        expired.expires_at = datetime.now(UTC) - timedelta(days=1)
        session.add(expired)
        session.commit()
        expired_chunk_id = _seed_chunk(session, expired, content="已过期正文").id

    # 检索面在开关关闭时只看 is_current（两篇都 is_current=True），核验面必须一致。
    with _as(peer) as client:
        assert client.get(_evidence(future_chunk_id)).status_code == 200
        assert client.get(_evidence(expired_chunk_id)).status_code == 200


# ── M-03：核验面 = 检索面（四角色逐个同真同假）──────────
async def test_evidence_matches_retrieval_face_for_every_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """对每个角色：``RAGService.search`` 能否命中 == ``/evidence`` 是否 200。

    检索侧用真实检索实测（不是只读 ``can_read_document``），否则等于用实现
    验证实现。这样 ``read_scope_for``（SQL 过滤）与 ``can_read_document``
    （行级判定）任一处单独偏移——收紧管理员或放开管理员——都会被抓红。
    """
    monkeypatch.setattr("app.rag.service.get_embedding_provider", lambda: MockEmbeddingProvider(dim=64))
    init_db()
    observed: list[str] = []
    for scope in (_TENANT_SCOPE, _UPLOADER_SCOPE):
        monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", scope)
        tenant = f"rag027-equiv-{scope}-{uuid4().hex[:8]}"
        marker = f"RAG027EQ{uuid4().hex[:10]}"
        owner = _user("rag027-eq-owner", tenant)
        peer = _user("rag027-eq-peer", tenant)
        tadmin = _user("rag027-eq-tadmin", tenant, Role.TENANT_ADMIN)
        sadmin = _user("rag027-eq-sadmin", tenant, Role.SYSTEM_ADMIN)
        roles = {
            "owner": owner,
            "peer": peer,
            "tenant_admin": tadmin,
            "system_admin": sadmin,
        }

        with Session(engine) as session:
            rag = RAGService(
                session, tenant, embedding_provider=MockEmbeddingProvider(dim=64), reader=owner
            )
            doc = await rag.ingest_text(
                f"{marker} 用于比对核验面与检索面的正文。",
                title="等价性文档",
                source=f"rag027-equiv-{scope}",
                user_id=owner.id,
            )
            chunk_id = session.exec(
                select(DocumentChunk).where(col(DocumentChunk.document_id) == doc.id)
            ).first()
            assert chunk_id is not None
            target_chunk_id = chunk_id.id

    observed: list[str] = []
    for scope in (_TENANT_SCOPE, _UPLOADER_SCOPE):
        monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", scope)
        expected = _EXPECTED_FACE[scope]
        retrieval: dict[str, bool] = {}
        verified: dict[str, bool] = {}
        for label, user in roles.items():
            with Session(engine) as session:
                hits = await RAGService(
                    session, tenant, embedding_provider=MockEmbeddingProvider(dim=64), reader=user
                ).search(marker, top_k=5)
                retrieval[label] = any(hit.id == target_chunk_id for hit in hits)
            with _as(user) as client:
                verified[label] = client.get(_evidence(target_chunk_id)).status_code == 200
        observed.append(f"{scope}: 检索={retrieval} 核验={verified}")
        # 两面同真同假：任一处单独偏移（收紧或放开管理员）都会在这里变红。
        assert retrieval == verified, (
            f"{scope} 核验面与检索面脱钩：检索={retrieval}，核验={verified}"
        )
        # 同时钉住每个角色的**期望取值**，避免「全都不可见」也能通过等价断言。
        assert retrieval == expected, f"{scope} 检索面取值与裁决不符：{retrieval} != {expected}"
    assert len(observed) == 2


# ── L-01：父块租户自洽 ────────────────────────────────
def test_parent_with_tampered_tenant_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """父子同文档，但父块行的 tenant_id 被拼改：整体不返回父块信息。"""
    monkeypatch.setattr("app.rag.access.settings.RAG_KB_SCOPE", _TENANT_SCOPE)
    init_db()
    tenant = f"rag027-partamper-{uuid4().hex[:8]}"
    owner = _user("rag027-pt-a", tenant)
    peer = _user("rag027-pt-b", tenant)
    with Session(engine) as session:
        doc = _seed_document(session, tenant_id=tenant, owner_id=owner.id, title="本租户文档")
        parent_id = _seed_chunk(
            session, doc, content="父块正文", chunk_index=0, tenant_id=f"{tenant}-other"
        ).id
        child_id = _seed_chunk(
            session, doc, content="子块正文", chunk_index=1, parent_id=parent_id
        ).id

    with _as(peer) as client:
        resp = client.get(_evidence(child_id, include_parent_content="true"))
    assert resp.status_code == 200
    assert resp.json()["parent"] is None
    assert "父块正文" not in json.dumps(resp.json(), ensure_ascii=False)
