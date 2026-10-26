import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.conversation import Conversation
from app.models.user import Tenant, User
from app.services.memory_policy import MemoryPolicyError, assert_memory_access


@pytest.fixture
def governed():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="a", name="A"))
        session.add(Tenant(id="b", name="B"))
        owner = User(id="owner", tenant_id="a", username="owner", hashed_password="", role="member")
        admin = User(id="admin", tenant_id="a", username="admin", hashed_password="", role="system_admin")
        other = User(id="other", tenant_id="b", username="other", hashed_password="", role="system_admin")
        session.add(owner)
        session.add(admin)
        session.add(other)
        session.add(Conversation(id="conv", tenant_id="a", user_id="owner"))
        session.commit()
        yield session, owner, admin, other, session.get(Conversation, "conv")
    engine.dispose()


def test_owner_and_same_tenant_system_admin_can_use_memory(governed):
    session, owner, admin, _, conversation = governed
    assert_memory_access(session, owner, conversation)
    assert_memory_access(session, admin, conversation)


def test_cross_tenant_revoked_deleted_and_disabled_are_rejected(governed):
    session, owner, _, other, conversation = governed
    with pytest.raises(MemoryPolicyError):
        assert_memory_access(session, other, conversation)
    owner.token_version += 1
    session.add(owner)
    session.commit()
    stale = User(
        id="owner", tenant_id="a", username="owner", hashed_password="", role="member", token_version=0
    )
    with pytest.raises(MemoryPolicyError):
        assert_memory_access(session, stale, conversation)
    owner.is_active = False
    session.add(owner)
    session.commit()
    with pytest.raises(MemoryPolicyError):
        assert_memory_access(session, owner, conversation)
    owner.is_active = True
    conversation.deleted_at = owner.updated_at
    session.add(owner)
    session.add(conversation)
    session.commit()
    with pytest.raises(MemoryPolicyError):
        assert_memory_access(session, owner, conversation)
