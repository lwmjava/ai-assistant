"""Owners can list, correct, invalidate, and export only their own memories."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.models.user_memory_preference  # noqa: F401
from app.agents.pipeline import AgentState
from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.main import app
from app.memory.cross_session import CrossSessionMemoryService
from app.models.conversation import Conversation, Message
from app.models.cross_session_memory import CrossSessionMemory
from app.models.user import Tenant, User
from app.rag.embeddings.factory import set_embedding_override
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.services.chat_service import ChatService
from app.services.memory_policy import MemoryPolicyError


@pytest.fixture
def memory_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="a", name="A"))
        session.add(Tenant(id="b", name="B"))
        session.add(User(id="owner", tenant_id="a", username="owner", hashed_password="", role="member"))
        session.add(User(id="other", tenant_id="a", username="other", hashed_password="", role="member"))
        session.add(
            User(id="admin", tenant_id="a", username="admin", hashed_password="", role="system_admin")
        )
        session.add(Conversation(id="saved", tenant_id="a", user_id="owner"))
        session.add(Message(id="fact", conversation_id="saved", role="user", content="工号是A12"))
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def cross_session(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_CROSS_SESSION_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "local")
    set_embedding_override(MockEmbeddingProvider(dim=32))
    yield
    set_embedding_override(None)


def _client(session: Session, user: User) -> TestClient:
    def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_owner_lists_corrects_and_hides_other_people(memory_db, cross_session):
    owner = memory_db.get(User, "owner")
    memory_id = await ChatService().remember_message(memory_db, owner, "saved", "fact")
    listed = _client(memory_db, owner).get("/api/v1/memories")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == memory_id
    assert "embedding" not in listed.json()[0]

    other = _client(memory_db, memory_db.get(User, "other"))
    assert other.get("/api/v1/memories").json() == []
    assert other.get(f"/api/v1/memories/{memory_id}").status_code == 404
    admin = _client(memory_db, memory_db.get(User, "admin"))
    assert admin.get(f"/api/v1/memories/{memory_id}").status_code == 404

    owner_client = _client(memory_db, owner)
    corrected = owner_client.post(
        f"/api/v1/memories/{memory_id}/corrections", json={"content": "工号是C56"}
    )
    assert corrected.status_code == 200
    assert corrected.json()["content"] == "工号是C56"
    assert corrected.json()["supersedes_id"] == memory_id
    again = owner_client.post(
        f"/api/v1/memories/{memory_id}/corrections", json={"content": "工号是C56"}
    )
    assert again.status_code == 404
    same = owner_client.post(
        f"/api/v1/memories/{corrected.json()['id']}/corrections", json={"content": "工号是C56"}
    )
    assert same.status_code == 200
    assert same.json()["id"] == corrected.json()["id"]
    assert len(memory_db.exec(select(CrossSessionMemory)).all()) == 2


@pytest.mark.asyncio
async def test_delete_is_idempotent_and_stops_recall(memory_db, cross_session):
    owner = memory_db.get(User, "owner")
    memory_id = await ChatService().remember_message(memory_db, owner, "saved", "fact")
    client = _client(memory_db, owner)
    first = client.delete(f"/api/v1/memories/{memory_id}")
    second = client.delete(f"/api/v1/memories/{memory_id}")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["invalidated"] is True
    assert second.json()["invalidated"] is True
    assert client.get("/api/v1/memories").json() == []
    state = AgentState(user_input="工号")
    await ChatService()._inject_cross_session(memory_db, owner, state, "工号")
    assert not state.context
    assert memory_db.get(CrossSessionMemory, memory_id) is not None


@pytest.mark.asyncio
async def test_export_redacts_personal_data_and_omits_embeddings(memory_db, cross_session):
    owner = memory_db.get(User, "owner")
    memory_db.add(
        CrossSessionMemory(
            id="phone-row",
            tenant_id="a",
            user_id="owner",
            user_token_version=0,
            source_conversation_id="saved",
            source_message_id="fact",
            content="联系电话是13800138000",
            embedding_json="[0.1]",
            embedding_provider="mock",
            embedding_model="mock-embedding",
            embedding_dim=1,
            policy_version="cross-session-memory-v1",
            source_grade="user_statement",
            content_hash="abc",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    memory_db.commit()
    exported = _client(memory_db, owner).get("/api/v1/memories/export")
    assert exported.status_code == 200
    body = exported.text
    assert "13800138000" not in body
    assert "***" in body
    assert "embedding" not in body


@pytest.mark.asyncio
async def test_user_switch_stops_read_and_write_but_keeps_rows(memory_db, cross_session):
    owner = memory_db.get(User, "owner")
    memory_id = await ChatService().remember_message(memory_db, owner, "saved", "fact")
    client = _client(memory_db, owner)
    closed = client.put("/api/v1/memories/settings", json={"enabled": False})
    assert closed.status_code == 200
    assert closed.json()["effective"] is False
    assert await ChatService().remember_message(memory_db, owner, "saved", "fact") is None
    state = AgentState(user_input="工号")
    await ChatService()._inject_cross_session(memory_db, owner, state, "工号")
    assert not state.context
    assert client.get("/api/v1/memories").json()[0]["id"] == memory_id
    blocked = client.post(
        f"/api/v1/memories/{memory_id}/corrections", json={"content": "工号是C56"}
    )
    assert blocked.status_code == 409
    opened = client.put("/api/v1/memories/settings", json={"enabled": True})
    assert opened.json()["effective"] is True


@pytest.mark.asyncio
async def test_personal_data_and_inactive_user_are_rejected(memory_db, cross_session):
    owner = memory_db.get(User, "owner")
    memory_id = await ChatService().remember_message(memory_db, owner, "saved", "fact")
    client = _client(memory_db, owner)
    rejected = client.post(
        f"/api/v1/memories/{memory_id}/corrections",
        json={"content": "联系电话是13800138000"},
    )
    assert rejected.status_code == 400
    owner.is_active = False
    memory_db.add(owner)
    memory_db.commit()
    with pytest.raises(MemoryPolicyError):
        CrossSessionMemoryService(memory_db).list_visible(owner)
