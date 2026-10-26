from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.llm.base import ChatMessage, LLMOptions
from app.models.conversation import Conversation, Message
from app.models.memory_snapshot import ConversationMemorySnapshot
from app.models.user import Tenant, User
from app.services.chat_service import ChatService
from app.services.memory_snapshots import MemorySnapshotService


@pytest.mark.asyncio
async def test_compression_failure_retains_full_pending_window():
    from app.llm.base import ChatRole
    from app.memory.base import MemoryConfig, MemorySnapshot
    from app.memory.manager import MemoryManager

    class FailingModel:
        async def chat(self, *args):
            raise RuntimeError("offline")

    messages = [ChatMessage(role=ChatRole.USER, content=str(i)) for i in range(12)]
    previous = MemorySnapshot(summary="earlier", compressed_count=2)
    manager = MemoryManager(FailingModel(), MemoryConfig(
        window_size=4, compression_threshold=5, keep_recent=2,
    ))
    result = await manager.manage(messages, existing_snapshot=previous)
    assert result.snapshot == previous
    assert [m.content for m in result.recent_messages] == ["8", "9", "10", "11"]


@pytest.mark.asyncio
async def test_chat_uses_configured_memory_and_invalidates_on_policy_change(memory_db, monkeypatch):
    from app.core.config import settings

    session, user = memory_db
    monkeypatch.setattr(settings, "MEMORY_WINDOW_SIZE", 8)
    monkeypatch.setattr(settings, "MEMORY_COMPRESSION_THRESHOLD", 10)
    monkeypatch.setattr(settings, "MEMORY_KEEP_RECENT", 3)
    conversation = session.get(Conversation, "conv")
    memory = await ChatService(SummaryLLM())._build_authorized_memory(session, user, conversation)
    assert memory.snapshot.compressed_count == 28
    assert len(memory.recent_messages) == 3
    monkeypatch.setattr(settings, "MEMORY_KEEP_RECENT", 4)
    assert MemorySnapshotService(session).load(user, conversation) is None


class SummaryLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def chat(self, messages: list[ChatMessage], options: LLMOptions | None = None) -> str:
        del options
        self.prompts.append(str(messages[-1].content))
        return "持久摘要"


@pytest.fixture
def memory_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="a", name="A"))
        user = User(id="owner", tenant_id="a", username="owner", hashed_password="", role="member")
        session.add(user)
        conversation = Conversation(id="conv", tenant_id="a", user_id="owner")
        session.add(conversation)
        start = datetime.now(UTC) - timedelta(minutes=10)
        for index in range(31):
            session.add(
                Message(
                    id=f"m-{index:03d}",
                    conversation_id="conv",
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"body-{index:03d}",
                    created_at=start + timedelta(seconds=index),
                    updated_at=start + timedelta(seconds=index),
                )
            )
        session.commit()
        yield session, user
    engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_survives_service_recreation_without_recompressing_old_messages(memory_db):
    session, user = memory_db
    llm = SummaryLLM()
    conversation = session.get(Conversation, "conv")
    first = await ChatService(llm)._build_authorized_memory(session, user, conversation)
    assert first.snapshot.compressed_count == 26
    assert len(llm.prompts) == 1
    session.expire_all()

    conversation = session.get(Conversation, "conv")
    second = await ChatService(llm)._build_authorized_memory(session, user, conversation)
    assert len(llm.prompts) == 1
    assert second.snapshot.summary == "持久摘要"
    assert [str(item.content) for item in second.recent_messages] == [
        f"body-{index:03d}" for index in range(26, 31)
    ]


def test_edit_expiry_revocation_and_cross_conversation_reject_snapshot(memory_db):
    session, user = memory_db
    conversation = session.get(Conversation, "conv")
    service = MemorySnapshotService(session)
    from app.memory.base import MemorySnapshot

    service.save(user, conversation, MemorySnapshot(summary="safe", compressed_count=26))
    message = session.get(Message, "m-000")
    message.content = "edited"
    session.add(message)
    session.commit()
    assert service.load(user, conversation) is None
    assert session.get(ConversationMemorySnapshot, "conv").invalidation_reason == "snapshot_binding_changed"

    other = Conversation(id="other-conv", tenant_id="a", user_id="owner")
    session.add(other)
    session.commit()
    assert service.load(user, other) is None

    service.save(user, conversation, MemorySnapshot(summary="safe", compressed_count=26))
    row = session.get(ConversationMemorySnapshot, "conv")
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.add(row)
    session.commit()
    assert service.load(user, conversation) is None
    assert session.get(ConversationMemorySnapshot, "conv") is not None

    service.save(user, conversation, MemorySnapshot(summary="safe", compressed_count=26))
    user.token_version += 1
    session.add(user)
    session.commit()
    assert service.load(user, conversation) is None


def test_conversation_delete_marks_snapshot_invalid(memory_db):
    session, user = memory_db
    conversation = session.get(Conversation, "conv")
    from app.memory.base import MemorySnapshot

    MemorySnapshotService(session).save(
        user, conversation, MemorySnapshot(summary="safe", compressed_count=26)
    )
    assert ChatService().delete_conversation(session, user, "conv") is True
    row = session.get(ConversationMemorySnapshot, "conv")
    assert row.invalidated_at is not None
    assert row.invalidation_reason == "conversation_deleted"
