"""CHAT-008：会话软删除与归档。"""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.core.database import engine
from app.core.security import Role
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.conversation import Conversation, Message
from app.models.user import Tenant, User


def _seed() -> tuple[User, TestClient]:
    with Session(engine) as session:
        tenant = Tenant(name="chat-008-tenant")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            username=f"chat008-{tenant.id[:8]}",
            hashed_password="x",
            role=Role.MEMBER.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.expunge(user)

    def _override() -> User:
        return user

    app.dependency_overrides[get_current_user] = _override
    set_llm_provider_override(MockLLMProvider())
    return user, TestClient(app)


def test_soft_delete_hides_from_default_list_but_keeps_rows() -> None:
    user, client = _seed()
    try:
        created = client.post("/api/v1/chat", json={"message": "保留内容"})
        assert created.status_code == 200
        conversation_id = created.json()["conversation_id"]

        deleted = client.delete(f"/api/v1/chat/conversations/{conversation_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        active = client.get("/api/v1/chat/conversations")
        assert active.status_code == 200
        assert active.json() == []

        removed = client.get("/api/v1/chat/conversations?status=deleted")
        assert removed.status_code == 200
        rows = removed.json()
        assert len(rows) == 1
        assert rows[0]["id"] == conversation_id
        assert rows[0]["deleted_at"]

        detail = client.get(f"/api/v1/chat/conversations/{conversation_id}")
        assert detail.status_code == 200
        assert any(m["role"] == "user" for m in detail.json()["messages"])

        with Session(engine) as session:
            conv = session.get(Conversation, conversation_id)
            assert conv is not None
            assert conv.deleted_at is not None
            messages = session.exec(
                select(Message).where(Message.conversation_id == conversation_id)
            ).all()
            assert len(messages) >= 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        set_llm_provider_override(None)


def test_archive_moves_to_archive_list() -> None:
    _user, client = _seed()
    try:
        created = client.post("/api/v1/chat", json={"message": "待归档"})
        assert created.status_code == 200
        conversation_id = created.json()["conversation_id"]

        archived = client.post(f"/api/v1/chat/conversations/{conversation_id}/archive")
        assert archived.status_code == 200
        assert archived.json()["archived_at"]

        active = client.get("/api/v1/chat/conversations?status=active")
        assert active.json() == []

        listed = client.get("/api/v1/chat/conversations?status=archived")
        assert len(listed.json()) == 1
        assert listed.json()[0]["id"] == conversation_id

        restored = client.post(f"/api/v1/chat/conversations/{conversation_id}/unarchive")
        assert restored.status_code == 200
        assert restored.json()["archived_at"] is None
        assert len(client.get("/api/v1/chat/conversations").json()) == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        set_llm_provider_override(None)
