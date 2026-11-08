"""组合验收：记得住、不串人、纠正、失效、过期、撤权和重启后仍守约。

真实对话和付费评测不在这里执行。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.models.user_memory_preference  # noqa: F401
from app.agents.pipeline import AgentState
from app.core.config import settings
from app.memory.cross_session import CrossSessionMemoryService
from app.models.conversation import Conversation, Message
from app.models.cross_session_memory import CrossSessionMemory
from app.models.user import Tenant, User
from app.rag.embeddings.factory import set_embedding_override
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.vectorstore.milvus import MilvusUnavailableError
from app.services.chat_service import ChatService


def _seed(session: Session) -> None:
    session.add(Tenant(id="a", name="A", is_active=True))
    session.add(Tenant(id="b", name="B", is_active=True))
    session.add(User(id="owner", tenant_id="a", username="owner", hashed_password="", role="member"))
    session.add(User(id="other", tenant_id="a", username="other", hashed_password="", role="member"))
    session.add(User(id="outsider", tenant_id="b", username="outsider", hashed_password="", role="member"))
    session.add(Conversation(id="saved", tenant_id="a", user_id="owner"))
    session.add(Conversation(id="later", tenant_id="a", user_id="owner"))
    session.add(Message(id="fact", conversation_id="saved", role="user", content="工号是A12"))
    session.add(Message(id="fixed", conversation_id="saved", role="user", content="工号是C56"))
    session.commit()


@pytest.fixture
def lifecycle(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_CROSS_SESSION_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "local")
    set_embedding_override(MockEmbeddingProvider(dim=32))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _seed(session)
        yield session, engine
    engine.dispose()
    set_embedding_override(None)


async def _context(session: Session, user: User, query: str) -> str:
    state = AgentState(user_input=query)
    await ChatService()._inject_cross_session(session, user, state, query)
    return state.context or ""


@pytest.mark.asyncio
async def test_saved_memory_is_recalled_after_a_new_session(lifecycle):
    session, engine = lifecycle
    owner = session.get(User, "owner")
    memory_id = await ChatService().remember_message(session, owner, "saved", "fact")
    assert "A12" in await _context(session, owner, "工号")
    session.close()
    with Session(engine) as restarted:
        owner = restarted.get(User, "owner")
        assert "A12" in await _context(restarted, owner, "工号")
        assert restarted.get(CrossSessionMemory, memory_id) is not None


@pytest.mark.asyncio
async def test_other_people_expiry_revocation_and_deletion_stay_hidden(lifecycle):
    session, _engine = lifecycle
    owner = session.get(User, "owner")
    memory_id = await ChatService().remember_message(session, owner, "saved", "fact")
    for user_id in ("other", "outsider"):
        assert await _context(session, session.get(User, user_id), "工号") == ""

    owner.token_version = 1
    session.add(owner)
    session.commit()
    assert await _context(session, owner, "工号") == ""
    owner.token_version = 0
    session.add(owner)
    session.commit()

    row = session.get(CrossSessionMemory, memory_id)
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(days=1)
    session.add(row)
    session.commit()
    assert await _context(session, owner, "工号") == ""
    row.expires_at = datetime.now(UTC) + timedelta(days=1)
    session.add(row)
    session.commit()

    conversation = session.get(Conversation, "saved")
    assert conversation is not None
    conversation.deleted_at = datetime.now(UTC)
    session.add(conversation)
    session.commit()
    assert await _context(session, owner, "工号") == ""
    assert session.get(CrossSessionMemory, memory_id) is not None


@pytest.mark.asyncio
async def test_correction_and_invalidation_change_what_can_be_recalled(lifecycle):
    session, _engine = lifecycle
    owner = session.get(User, "owner")
    service = ChatService()
    memory_id = await service.remember_message(session, owner, "saved", "fact")
    corrected = await service.correct_memory(session, owner, memory_id, "saved", "fixed")
    recalled = await _context(session, owner, "工号")
    assert "C56" in recalled
    assert "A12" not in recalled
    CrossSessionMemoryService(session).invalidate(owner, corrected)
    assert await _context(session, owner, "工号") == ""
    assert session.get(CrossSessionMemory, memory_id) is not None
    assert session.get(CrossSessionMemory, corrected) is not None


@pytest.mark.asyncio
async def test_recall_failure_does_not_inject_a_local_copy(lifecycle, monkeypatch):
    session, _engine = lifecycle
    owner = session.get(User, "owner")
    await ChatService().remember_message(session, owner, "saved", "fact")
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "milvus")

    async def fail_search(*_args, **_kwargs):
        raise MilvusUnavailableError("offline")

    monkeypatch.setattr("app.rag.vectorstore.memory_index.search_ids", fail_search)
    assert await _context(session, owner, "工号") == ""
