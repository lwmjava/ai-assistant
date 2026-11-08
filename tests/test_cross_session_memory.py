"""Cross-session memory stores metadata beside the configured vector store."""

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.agents.pipeline import AgentState
from app.core.config import settings
from app.models.conversation import Conversation, Message
from app.models.cross_session_memory import CrossSessionMemory
from app.models.user import Tenant, User
from app.rag.embeddings.factory import set_embedding_override
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.vectorstore.milvus import MilvusUnavailableError
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
        owner = User(
            id="owner", tenant_id="a", username="owner", hashed_password="", role="member"
        )
        other = User(
            id="other", tenant_id="a", username="other", hashed_password="", role="member"
        )
        admin = User(
            id="admin", tenant_id="a", username="admin", hashed_password="", role="system_admin"
        )
        outsider = User(
            id="outsider", tenant_id="b", username="outsider", hashed_password="", role="member"
        )
        session.add(owner)
        session.add(other)
        session.add(admin)
        session.add(outsider)
        session.add(Conversation(id="saved", tenant_id="a", user_id="owner"))
        session.add(Conversation(id="later", tenant_id="a", user_id="owner"))
        session.add(
            Message(
                id="fact",
                conversation_id="saved",
                role="user",
                content="项目代号青鸟将在周五发布",
            )
        )
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


@pytest.mark.asyncio
async def test_saved_memory_is_injected_into_a_later_conversation(memory_db, cross_session):
    service = ChatService()
    memory_id = await service.remember_message(memory_db, memory_db.get(User, "owner"), "saved", "fact")
    row = memory_db.get(CrossSessionMemory, memory_id)
    assert row is not None
    assert row.source_conversation_id == "saved"
    assert row.source_message_id == "fact"
    state = AgentState(user_input="项目代号是什么")
    await service._inject_cross_session(
        memory_db, memory_db.get(User, "owner"), state, "项目代号是什么"
    )
    assert state.context is not None
    assert "项目代号青鸟" in state.context
    assert "saved" in state.context
    assert "fact" in state.context


def test_dialogue_entries_inject_recalled_memory():
    chat = inspect.getsource(ChatService.chat)
    stream = inspect.getsource(ChatService.chat_stream)
    assert "await self._inject_cross_session" in chat
    assert "await self._inject_cross_session" in stream


@pytest.mark.asyncio
async def test_closed_switch_keeps_rows_but_stops_read_and_write(memory_db, cross_session, monkeypatch):
    owner = memory_db.get(User, "owner")
    await ChatService().remember_message(memory_db, owner, "saved", "fact")
    monkeypatch.setattr(settings, "MEMORY_CROSS_SESSION_ENABLED", False)
    assert await ChatService().remember_message(memory_db, owner, "saved", "fact") is None
    assert len(memory_db.exec(select(CrossSessionMemory)).all()) == 1
    state = AgentState(user_input="项目代号")
    await ChatService()._inject_cross_session(memory_db, owner, state, "项目代号")
    assert not state.context


@pytest.mark.asyncio
async def test_other_user_tenant_revocation_expiry_and_delete_are_hidden(
    memory_db, cross_session
):
    owner = memory_db.get(User, "owner")
    await ChatService().remember_message(memory_db, owner, "saved", "fact")
    service = ChatService()
    for user_id in ("other", "outsider"):
        state = AgentState(user_input="项目代号")
        await service._inject_cross_session(
            memory_db, memory_db.get(User, user_id), state, "项目代号"
        )
        assert not state.context

    admin_state = AgentState(user_input="项目代号")
    await service._inject_cross_session(
        memory_db, memory_db.get(User, "admin"), admin_state, "项目代号"
    )
    assert admin_state.context is not None
    assert "项目代号青鸟" in admin_state.context

    owner.token_version = 1
    memory_db.add(owner)
    memory_db.commit()
    revoked = AgentState(user_input="项目代号")
    await service._inject_cross_session(memory_db, owner, revoked, "项目代号")
    assert not revoked.context

    owner.token_version = 0
    memory_db.add(owner)
    row = memory_db.exec(select(CrossSessionMemory)).one()
    row.expires_at = datetime.now(UTC) - timedelta(days=1)
    memory_db.add(row)
    memory_db.commit()
    expired = AgentState(user_input="项目代号")
    await service._inject_cross_session(memory_db, owner, expired, "项目代号")
    assert not expired.context

    row.expires_at = datetime.now(UTC) + timedelta(days=1)
    conversation = memory_db.get(Conversation, "saved")
    conversation.deleted_at = datetime.now(UTC)
    memory_db.add(row)
    memory_db.add(conversation)
    memory_db.commit()
    deleted = AgentState(user_input="项目代号")
    await service._inject_cross_session(memory_db, owner, deleted, "项目代号")
    assert not deleted.context
    assert memory_db.exec(select(col(CrossSessionMemory.id))).all()


@pytest.mark.asyncio
async def test_member_cannot_save_someone_elses_message(memory_db, cross_session):
    with pytest.raises(MemoryPolicyError):
        await ChatService().remember_message(
            memory_db, memory_db.get(User, "other"), "saved", "fact"
        )


@pytest.mark.asyncio
async def test_milvus_failure_does_not_rank_the_local_copy(memory_db, cross_session, monkeypatch):
    owner = memory_db.get(User, "owner")
    await ChatService().remember_message(memory_db, owner, "saved", "fact")
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "milvus")

    async def fail_search(*_args, **_kwargs):
        raise MilvusUnavailableError("offline")

    monkeypatch.setattr("app.rag.vectorstore.memory_index.search_ids", fail_search)
    state = AgentState(user_input="项目代号")
    await ChatService()._inject_cross_session(memory_db, owner, state, "项目代号")
    assert not state.context


@pytest.mark.asyncio
async def test_milvus_write_failure_does_not_keep_a_searchable_row(
    memory_db, cross_session, monkeypatch
):
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "milvus")

    async def fail_upsert(*_args, **_kwargs):
        raise MilvusUnavailableError("offline")

    monkeypatch.setattr("app.rag.vectorstore.memory_index.upsert", fail_upsert)
    with pytest.raises(MilvusUnavailableError):
        await ChatService().remember_message(
            memory_db, memory_db.get(User, "owner"), "saved", "fact"
        )
    assert memory_db.exec(select(CrossSessionMemory)).all() == []
