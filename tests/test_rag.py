"""RAG 检索增强测试。

覆盖：文档摄取与混合检索、文档生命周期（列表 / 详情 / 删除级联）、
检索接口、检索器作为管线钩子的可用性。嵌入模型在开发环境自动降级为 Mock，
无需真实嵌入 API 即可运行。
"""

import io

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import engine
from app.main import app
from app.core.security import Role
from app.models.user import User
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.ingestion import split_text
from app.rag.service import RAGService


@pytest.fixture()
def client():
    fake_user = User(
        id="rag-user",
        tenant_id="rag-tenant",
        username="rag-tester",
        hashed_password="",
        role=Role.MEMBER.value,
        token_version=0,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def session():
    with Session(engine) as s:
        yield s


# ── 分块 ──────────────────────────────────────────
def test_split_text_basic() -> None:
    text = "第一句。第二句。第三句很长需要被保留。" * 10
    chunks = split_text(text, chunk_size=50, chunk_overlap=10)
    assert chunks
    assert all(len(c) <= 60 for c in chunks)  # 含重叠不超过 chunk_size + overlap 太多
    joined = "".join(chunks)
    assert "第一句" in joined and "第三句" in joined


def test_split_text_empty() -> None:
    assert split_text("") == []
    assert split_text("   \n  ") == []


# ── 摄取与混合检索（服务层）────────────────────────
async def test_ingest_and_search(session: Session) -> None:
    rag = RAGService(session, "unit-tenant")
    doc = await rag.ingest_text(
        "检索增强生成（RAG）通过引入外部知识提升回答准确性。"
        "向量数据库负责存储嵌入，混合检索结合稠密与稀疏信号。",
        title="RAG 概念",
        source="unit",
        user_id="unit-user",
    )
    assert doc.chunk_count > 0

    results = await rag.search("什么是混合检索", top_k=3)
    assert results
    # 查询词「检索」应命中包含该词的块。
    assert any("检索" in r.content for r in results)


async def test_retriever_as_pipeline_hook(session: Session) -> None:
    rag = RAGService(session, "hook-tenant", embedding_provider=MockEmbeddingProvider(dim=256))
    await rag.ingest_text(
        "Milvus 是可水平扩展的向量数据库，适合大规模嵌入检索。",
        title="向量库",
        source="hook",
        user_id="hook-user",
    )
    retriever = rag.make_retriever(top_k=3)
    context = await retriever.retrieve("Milvus 适合什么场景", "规划：查找向量库资料")
    assert "Milvus" in context
    assert "资料 1" in context


# ── 文档生命周期（接口层）──────────────────────────
def test_document_lifecycle(client: TestClient) -> None:
    ingest = client.post(
        "/api/rag/documents/ingest",
        json={
            "text": "知识库检索允许用户用自然语言提问并获取相关段落。",
            "title": "知识库说明",
            "source": "lifecycle",
        },
    )
    assert ingest.status_code == 200
    doc_id = ingest.json()["id"]
    assert ingest.json()["chunk_count"] > 0

    listing = client.get("/api/rag/documents")
    assert listing.status_code == 200
    assert any(d["id"] == doc_id for d in listing.json())

    detail = client.get(f"/api/rag/documents/{doc_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == doc_id

    deleted = client.delete(f"/api/rag/documents/{doc_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    missing = client.get(f"/api/rag/documents/{doc_id}")
    assert missing.status_code == 404


def test_ingest_validation(client: TestClient) -> None:
    resp = client.post(
        "/api/rag/documents/ingest", json={"text": "", "title": ""}
    )
    assert resp.status_code == 400


def test_upload_rejects_non_text(client: TestClient) -> None:
    files = {"file": ("data.bin", io.BytesIO(b"\x00\x01\x02"), "application/octet-stream")}
    resp = client.post("/api/rag/documents/upload", files=files)
    assert resp.status_code == 400


def test_search_endpoint(client: TestClient) -> None:
    client.post(
        "/api/rag/documents/ingest",
        json={
            "text": "Agent 编排将任务拆解为理解、规划、行动、反思、响应五个阶段。",
            "title": "Agent 流程",
            "source": "search",
        },
    )
    resp = client.post("/api/rag/search", json={"query": "Agent 有几个阶段", "top_k": 3})
    assert resp.status_code == 200
    assert any("阶段" in r["content"] for r in resp.json())


def test_search_empty_query(client: TestClient) -> None:
    resp = client.post("/api/rag/search", json={"query": "  "})
    assert resp.status_code == 400


# ── 管线接线 ──────────────────────────────────────
def test_build_retriever_respects_flag(session: Session) -> None:
    from app.services.chat_service import ChatService

    user = User(
        id="w-user",
        tenant_id="w-tenant",
        username="w",
        hashed_password="",
        role=Role.MEMBER.value,
        token_version=0,
        is_active=True,
    )
    svc = ChatService()
    original = settings.RAG_ENABLED
    try:
        settings.RAG_ENABLED = False
        assert svc._build_retriever(session, user) is None
        settings.RAG_ENABLED = True
        assert svc._build_retriever(session, user) is not None
    finally:
        settings.RAG_ENABLED = original
