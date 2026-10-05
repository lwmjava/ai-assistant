"""文档分块详情接口测试。

覆盖：按块序返回、字段完整、数量与 chunk_count 一致、未知文档 404、普通成员可读自己文档。
嵌入模型在开发环境自动降级为 Mock，无需真实嵌入 API。
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.security import Role
from app.main import app
from app.models.user import User


@pytest.fixture()
def admin_client():
    fake_user = User(
        id="chunk-admin",
        tenant_id="chunk-tenant",
        username="chunk-admin",
        hashed_password="",
        role=Role.TENANT_ADMIN.value,
        token_version=0,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def member_client():
    fake_user = User(
        id="chunk-member",
        tenant_id="chunk-tenant",
        username="chunk-member",
        hashed_password="",
        role=Role.MEMBER.value,
        token_version=0,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _ingest(client: TestClient, text: str, title: str) -> dict:
    r = client.post(
        "/api/rag/documents/ingest",
        json={"text": text, "title": title, "source": "chunk-test"},
    )
    assert r.status_code == 200
    return r.json()


def test_document_chunks_returns_indexed_blocks(admin_client: TestClient) -> None:
    doc = _ingest(admin_client, "第一段内容。第二段内容。第三段内容。", "分块测试")
    r = admin_client.get(f"/api/rag/documents/{doc['id']}/chunks")
    assert r.status_code == 200
    chunks = r.json()
    assert isinstance(chunks, list)
    assert len(chunks) == doc["chunk_count"]
    assert len(chunks) > 0
    # 按块序升序
    indexes = [c["chunk_index"] for c in chunks]
    assert indexes == sorted(indexes)
    # 字段齐全
    first = chunks[0]
    for key in ("id", "chunk_index", "content", "source", "strategy", "page", "section", "created_at"):
        assert key in first


def test_document_chunks_unknown_document_404(admin_client: TestClient) -> None:
    r = admin_client.get("/api/rag/documents/not-exist/chunks")
    assert r.status_code == 404


def test_document_chunks_member_can_read_own_document(member_client: TestClient) -> None:
    doc = _ingest(member_client, "成员自己的文档，用于校验分块读取权限。", "成员分块")
    r = member_client.get(f"/api/rag/documents/{doc['id']}/chunks")
    assert r.status_code == 200
    assert len(r.json()) == doc["chunk_count"]


def test_document_chunks_concatenated_content_covers_text(admin_client: TestClient) -> None:
    text = "知识库检索允许用户用自然语言提问并获取相关段落。这是第二句，用于校验分块内容完整性。"
    doc = _ingest(admin_client, text, "内容覆盖")
    r = admin_client.get(f"/api/rag/documents/{doc['id']}/chunks")
    chunks = r.json()
    joined = "".join(c["content"] for c in chunks)
    # 分块拼接应覆盖原文主体（Mock 分块策略可能裁剪空白，故用包含性断言）
    core = "知识库检索允许用户用自然语言提问"
    assert core in joined
