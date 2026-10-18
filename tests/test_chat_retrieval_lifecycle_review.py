"""独立入口反例：隔离 SQLite/真实 local 检索，合成模型只证明契约。"""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, col, create_engine, select

import app.rag.service as rag_module
from app.agents.prompts import SYSTEM_PREFLOW
from app.core.config import settings
from app.llm.mock import MockLLMProvider
from app.models.conversation import Message
from app.models.rag import DocumentChunk
from app.models.user import Tenant, User
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.retriever import assemble_retrieval
from app.rag.service import RAGService
from app.services.chat_attachments import PreparedAttachment
from app.services.chat_service import ChatService


class ReviewModel(MockLLMProvider):
    def __init__(self):
        super().__init__()
        self.inputs = []

    async def chat(self, messages, options=None):
        self.inputs.extend(str(message.content) for message in messages)
        if messages[0].content == SYSTEM_PREFLOW:
            return "YES"
        return await super().chat(messages, options)


@pytest.fixture
def review_db(tmp_path, monkeypatch):
    for name, value in {
        "RAG_ENABLED": True, "RAG_BACKEND": "native", "RAG_VECTOR_STORE": "local",
        "RAG_KB_SCOPE": "uploader", "RAG_MIN_SIMILARITY": 0.0,
        "RAG_CONTEXT_CHARS": 12000,
        "RETRIEVAL_CACHE_ENABLED": False, "LLM_RESPONSE_CACHE_ENABLED": False,
        "AGENT_ORCHESTRATION": "self", "SKILL_ENABLED": False, "MCP_ENABLED": False,
        "MEMORY_ENABLED": False, "AGENT_QUALITY_GATE_ENABLED": False,
        "SECURITY_RATE_LIMIT": False,
    }.items():
        if hasattr(settings, name):
            monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(rag_module, "get_embedding_provider", lambda: MockEmbeddingProvider(dim=64))
    engine = create_engine(f"sqlite:///{tmp_path / 'entry-review.db'}")
    SQLModel.metadata.create_all(engine)
    tenant = uuid4().hex
    actor = User(id=uuid4().hex, tenant_id=tenant, username=uuid4().hex, hashed_password="")
    with Session(engine) as db:
        db.add(Tenant(id=tenant, name=tenant))
        db.add(actor)
        db.commit()
        db.refresh(actor)
        db.expunge(actor)
    yield engine, actor
    engine.dispose()


async def seed_parent(db, tenant, owner, marker, parent_text):
    rag = RAGService(db, tenant, embedding_provider=MockEmbeddingProvider(dim=64))
    document = await rag.ingest_text(f"{marker} 年假政策正文。", marker, marker, owner)
    child = db.exec(select(DocumentChunk).where(col(DocumentChunk.document_id) == document.id)).first()
    assert child is not None
    parent = DocumentChunk(tenant_id=tenant, document_id=document.id, content=parent_text,
                           source=marker, chunk_metadata='{"kind":"parent"}', index_id=child.index_id)
    db.add(parent)
    db.flush()
    child.parent_id = parent.id
    db.add(child)
    db.commit()
    return document.id, child.id, parent.id


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("scope", ["tenant", "uploader"])
@pytest.mark.parametrize("orchestration", ["self", "supervisor"])
async def test_real_chat_keeps_authorized_parent_sources(review_db, monkeypatch, streaming, scope, orchestration):
    engine, actor = review_db
    monkeypatch.setattr(settings, "RAG_KB_SCOPE", scope)
    marker = "年假证据"
    with Session(engine) as db:
        own, _, parent = await seed_parent(db, actor.tenant_id, actor.id, marker, "OWN_PARENT_VISIBLE")
        peer, _, _ = await seed_parent(db, actor.tenant_id, "peer", marker, "PEER_PARENT_PRIVATE")
        foreign, _, _ = await seed_parent(db, "foreign", "foreign", marker, "FOREIGN_PARENT_PRIVATE")
    model = ReviewModel()
    service = ChatService(model)
    if orchestration == "supervisor":
        monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "langgraph")
    # 已准备附件明确禁止 fast route；langgraph 时必须经过真实 Supervisor。
    kwargs = {"attachments_prepared": [PreparedAttachment("memo.md", 4, "text/plain", text="附件备注")]}
    with Session(engine) as db:
        if streaming:
            events = [event async for event in service.chat_stream(db, actor, marker, **kwargs)]
            assert not any(event.type == "error" for event in events)
        else:
            await service.chat(db, actor, marker, **kwargs)
    with Session(engine) as fresh:
        answers = list(fresh.exec(select(Message).where(col(Message.role) == "assistant")).all())
        assert len(answers) == 1
        sources = json.loads(answers[0].sources or "[]")
    assert sources
    encoded = json.dumps(sources)
    assert own in encoded and parent in encoded
    assert foreign not in encoded
    payload = "\n".join(model.inputs)
    assert "附件备注" in payload
    assert "OWN_PARENT_VISIBLE" in payload and "FOREIGN_PARENT_PRIVATE" not in payload
    if scope == "uploader":
        assert peer not in encoded and "PEER_PARENT_PRIVATE" not in payload
    else:
        assert peer in encoded and "PEER_PARENT_PRIVATE" in payload


@pytest.mark.parametrize("streaming", [False, True])
async def test_service_budget_does_not_claim_unselected_sources(review_db, monkeypatch, streaming):
    engine, actor = review_db
    with Session(engine) as db:
        await seed_parent(db, actor.tenant_id, actor.id, "年假政策", "PARENT_MUST_NOT_LEAK")
    monkeypatch.setattr(settings, "RAG_CONTEXT_CHARS", 1)
    model = ReviewModel()
    with Session(engine) as db:
        service = ChatService(model)
        if streaming:
            _events = [event async for event in service.chat_stream(db, actor, "年假政策")]
        else:
            await service.chat(db, actor, "年假政策")
    with Session(engine) as fresh:
        answer = fresh.exec(select(Message).where(col(Message.role) == "assistant")).one()
        assert json.loads(answer.sources or "[]") == []
    assert "PARENT_MUST_NOT_LEAK" not in "\n".join(model.inputs)


@pytest.mark.parametrize("streaming", [False, True])
async def test_service_dependency_cancellation_is_not_unavailable(review_db, monkeypatch, streaming):
    engine, actor = review_db
    calls = []

    def cancel():
        calls.append(1)
        raise asyncio.CancelledError()

    monkeypatch.setattr(rag_module, "get_embedding_provider", cancel)
    with Session(engine) as db:
        service = ChatService(ReviewModel())
        with pytest.raises(asyncio.CancelledError):
            if streaming:
                _events = [event async for event in service.chat_stream(db, actor, "年假政策")]
            else:
                await service.chat(db, actor, "年假政策")
    assert calls == [1]
    with Session(engine) as fresh:
        answers = list(fresh.exec(select(Message).where(col(Message.role) == "assistant")).all())
        assert all(settings.RAG_UNAVAILABLE_REPLY not in answer.content for answer in answers)


@pytest.mark.parametrize("cache", [False, True])
async def test_lazy_reader_is_frozen_before_orm_identity_mutation(review_db, monkeypatch, cache):
    engine, actor = review_db
    monkeypatch.setattr(settings, "RETRIEVAL_CACHE_ENABLED", cache)
    tenant, owner = actor.tenant_id, actor.id
    with Session(engine) as db:
        own, _, _ = await seed_parent(db, tenant, owner, "年假", "OWN_SNAPSHOT_PARENT")
        peer, _, _ = await seed_parent(db, tenant, "peer", "年假", "PEER_SNAPSHOT_PRIVATE")
        foreign, _, _ = await seed_parent(db, "foreign", "foreign", "年假", "FOREIGN_SNAPSHOT_PRIVATE")
        service = ChatService(ReviewModel())
        hook = service._build_retriever(db, actor)
        actor.id, actor.tenant_id, actor.role = "peer", "foreign", "system_admin"
        snippet = await hook.retrieve("年假", "")
        payload = assemble_retrieval(hook, snippet)
        encoded = json.dumps(service._reply_sources(db, hook))
        assert own in encoded and peer not in encoded and foreign not in encoded
        assert "OWN_SNAPSHOT_PARENT" in payload.text
        assert "PEER_SNAPSHOT_PRIVATE" not in payload.text and "FOREIGN_SNAPSHOT_PRIVATE" not in payload.text

