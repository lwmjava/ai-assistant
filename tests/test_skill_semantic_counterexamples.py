"""Independent entry review: committed authorization races and persistence (E2)."""

import asyncio
import json

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.llm.mock import MockLLMProvider
from app.models.conversation import Message
from app.models.skill import Skill
from app.models.user import Tenant, User
from app.services.agent_execution_guard import ResumeRejectedError, _active
from app.services.chat_service import ChatService


@pytest.fixture
def review_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SKILL_ENABLED", True)
    monkeypatch.setattr("app.services.skill_service.discover_skills", lambda: [])
    engine = create_engine(f"sqlite:///{tmp_path / 'entry-review.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="review-alpha", name="review-alpha"))
        session.add(User(id="review-ada", username="review-ada", tenant_id="review-alpha",
                         hashed_password="synthetic-only"))
        session.add(Skill(id="review-parcel", name="parcel", name_key="review-parcel",
                          tenant_id="review-alpha", owner_id="review-ada", scope="private",
                          description="Explain shipping progress", constraints="Explanation only",
                          system_prompt="SYNTHETIC_PRIVATE_BODY", example="Shipping explanation",
                          keywords='["parcel-code"]'))
        session.commit()
        yield engine, session, session.get(User, "review-ada")
    engine.dispose()


class ReviewProvider(MockLLMProvider):
    def __init__(self, change=None):
        super().__init__(model="review-selector")
        self.change = change
        self.selections = []

    async def chat(self, messages, options=None):
        if messages[0].content.startswith("Select at most one skill"):
            self.selections.append((messages, options))
            if self.change is not None:
                self.change()
            return json.dumps({"name": "parcel", "confidence": 0.95})
        return "SYNTHETIC_SUCCESSFUL_REPLY"


@pytest.mark.parametrize("mutation", ["disable", "delete", "owner", "tenant", "body", "version"])
def test_committed_skill_change_during_selection_is_rejected(review_db, mutation):
    engine, session, user = review_db

    def change():
        with Session(engine) as writer:
            row = writer.get(Skill, "review-parcel")
            if mutation == "delete":
                writer.delete(row)
            else:
                field, value = {"disable": ("enabled", False), "owner": ("owner_id", "other"),
                                "tenant": ("tenant_id", "other"), "body": ("system_prompt", "changed"),
                                "version": ("version", "2.0")}[mutation]
                setattr(row, field, value)
                writer.add(row)
            writer.commit()

    provider = ReviewProvider(change)
    result = asyncio.run(ChatService(provider)._match_skills_async(session, user, "Explain delivery status"))
    assert result is None
    assert len(provider.selections) == 1


@pytest.mark.parametrize("principal", ["user", "tenant", "role", "token", "move", "delete"])
def test_committed_principal_revocation_during_selection_is_rejected(review_db, principal):
    engine, session, user = review_db

    def change():
        with Session(engine) as writer:
            row = writer.get(Tenant, user.tenant_id) if principal == "tenant" else writer.get(User, user.id)
            if principal == "delete":
                writer.delete(row)
            else:
                if principal in {"user", "tenant"}:
                    row.is_active = False
                elif principal == "role":
                    row.role = "tenant_admin"
                elif principal == "token":
                    row.token_version += 1
                else:
                    row.tenant_id = "other"
                writer.add(row)
            writer.commit()

    provider = ReviewProvider(change)
    with pytest.raises(ResumeRejectedError) as error:
        asyncio.run(ChatService(provider)._match_skills_async(session, user, "Explain delivery status"))
    assert error.value.status_code == 403


@pytest.mark.parametrize("streaming", [False, True])
def test_real_chat_persists_semantic_skill_readable_from_new_session(review_db, monkeypatch, streaming):
    engine, session, user = review_db
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "langgraph")
    monkeypatch.setattr(settings, "SECURITY_ENABLED", False)
    monkeypatch.setattr(settings, "RAG_ENABLED", False)
    monkeypatch.setattr(settings, "MCP_ENABLED", False)
    monkeypatch.setattr(settings, "EVOLUTION_ENABLED", False)
    provider = ReviewProvider()
    service = ChatService(provider)

    async def invoke():
        if streaming:
            events = [event async for event in service.chat_stream(session, user, "Explain delivery status")]
            assert any(event.type == "done" for event in events)
        else:
            _, answer = await service.chat(session, user, "Explain delivery status")
            assert answer == "SYNTHETIC_SUCCESSFUL_REPLY"

    asyncio.run(invoke())
    with Session(engine) as reader:
        messages = reader.exec(select(Message).where(Message.role == "assistant")).all()
        assert len(messages) == 1
        assert messages[0].content == "SYNTHETIC_SUCCESSFUL_REPLY"
        assert json.loads(messages[0].skill_names) == ["parcel"]
    assert len(provider.selections) == 1
    selector_text = "\n".join(item.content for item in provider.selections[0][0])
    assert "SYNTHETIC_PRIVATE_BODY" not in selector_text


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("principal", ["user", "tenant"])
def test_real_chat_revocation_stops_reply_and_releases_execution(review_db, monkeypatch, streaming, principal):
    engine, session, user = review_db
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "langgraph")
    monkeypatch.setattr(settings, "SECURITY_ENABLED", False)
    monkeypatch.setattr(settings, "RAG_ENABLED", False)
    monkeypatch.setattr(settings, "MCP_ENABLED", False)
    monkeypatch.setattr(settings, "EVOLUTION_ENABLED", False)
    baseline = set(_active)

    def revoke():
        with Session(engine) as writer:
            row = writer.get(User, "review-ada") if principal == "user" else writer.get(Tenant, "review-alpha")
            row.is_active = False
            writer.add(row)
            writer.commit()

    service = ChatService(ReviewProvider(revoke))

    async def invoke():
        if streaming:
            async for _ in service.chat_stream(session, user, "Explain delivery status"):
                pass
        else:
            await service.chat(session, user, "Explain delivery status")

    with pytest.raises(ResumeRejectedError) as error:
        asyncio.run(invoke())
    assert error.value.status_code == 403
    assert set(_active) == baseline
    with Session(engine) as reader:
        messages = reader.exec(select(Message)).all()
        assert [message.role for message in messages] == ["user"]
