"""从认证API进入重新生成，使用隔离主体和合成模型回查持久化结果。"""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.api.deps import get_current_user
from app.core.database import engine
from app.core.security import Role
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.conversation import Conversation, Message
from app.models.user import Tenant, User


class RecordingModel(MockLLMProvider):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.fail = False
        self.revoke = None

    async def chat(self, messages, options=None):
        self.requests.append(messages)
        if self.revoke:
            with Session(engine) as session:
                user = session.get(User, self.revoke)
                user.is_active = False
                session.add(user)
                session.commit()
            self.revoke = None
        if self.fail:
            raise RuntimeError("synthetic-private-provider-error")
        return await super().chat(messages, options)


@pytest.fixture
def regeneration(persist_identity):
    identity = "regenerate-" + secrets.token_hex(6)
    owner = User(id=identity, tenant_id=identity, username=identity, hashed_password="", role=Role.MEMBER.value)
    persist_identity(owner)
    now = datetime.now(UTC)
    with Session(engine) as session:
        conv = Conversation(tenant_id=identity, user_id=identity)
        session.add(conv)
        session.flush()
        question = Message(conversation_id=conv.id, role="user", content="你好，介绍一下你自己", created_at=now)
        answer = Message(conversation_id=conv.id, role="assistant", content="original-answer-marker",
                         created_at=now + timedelta(microseconds=1))
        session.add(question)
        session.add(answer)
        session.commit()
        ids = conv.id, question.id, answer.id
    model = RecordingModel()
    set_llm_provider_override(model)
    holder = {"user": owner}
    app.dependency_overrides[get_current_user] = lambda: holder["user"]
    try:
        with TestClient(app) as client:
            yield client, holder, model, ids
    finally:
        app.dependency_overrides.clear()
        set_llm_provider_override(None)


def _url(ids, target=None):
    return f"/api/v1/chat/conversations/{ids[0]}/messages/{target or ids[2]}/regenerate"


def _messages(conversation_id):
    with Session(engine) as session:
        return list(session.exec(select(Message).where(col(Message.conversation_id) == conversation_id)
                                 .order_by(col(Message.created_at), col(Message.id))).all())


def test_regenerate_appends_distinct_reply_and_keeps_original(regeneration):
    client, _holder, model, ids = regeneration
    response = client.post(_url(ids))
    assert response.status_code == 200, response.text
    rows = _messages(ids[0])
    assert len(rows) == 4
    assert [row.content for row in rows if row.id == ids[2]] == ["original-answer-marker"]
    assert rows[-1].role == "assistant" and rows[-1].id != ids[2]
    assert rows[-2].role == "user" and rows[-2].id != ids[1]
    assert response.json()["message_id"] == rows[-2].id
    assert model.requests
    detail = client.get(f"/api/v1/chat/conversations/{ids[0]}").json()
    assert {ids[2], rows[-1].id} <= {row["id"] for row in detail["messages"]}
    count = len(model.requests)
    assert client.post(_url(ids)).status_code == 409
    assert len(model.requests) == count and len(_messages(ids[0])) == 4


@pytest.mark.parametrize("target", ["unknown", "user", "pending"])
def test_invalid_or_stale_target_has_no_side_effect(regeneration, target):
    client, _holder, model, ids = regeneration
    if target == "pending":
        with Session(engine) as session:
            session.add(Message(conversation_id=ids[0], role="user", content="new pending request"))
            session.commit()
    before = len(_messages(ids[0]))
    response = client.post(_url(ids, ids[1] if target == "user" else "not-a-message" if target == "unknown" else None))
    assert response.status_code == (404 if target == "unknown" else 409)
    assert not model.requests and len(_messages(ids[0])) == before


@pytest.mark.parametrize("mode", ["peer", "cross_tenant", "viewer"])
def test_regenerate_obeys_identity_and_tenant(regeneration, persist_identity, mode):
    client, holder, model, ids = regeneration
    owner = holder["user"]
    identity = "other-" + secrets.token_hex(6)
    user = User(id=identity, tenant_id=identity if mode == "cross_tenant" else owner.tenant_id,
                username=identity, hashed_password="",
                role=Role.VIEWER.value if mode == "viewer" else Role.MEMBER.value)
    persist_identity(user)
    holder["user"] = user
    response = client.post(_url(ids))
    assert response.status_code == (403 if mode == "viewer" else 404)
    assert not model.requests and len(_messages(ids[0])) == 2


def test_provider_failure_keeps_original_reply(regeneration):
    client, _holder, model, ids = regeneration
    model.fail = True
    response = client.post(_url(ids))
    assert response.status_code == 502
    assert "synthetic-private" not in response.text
    rows = _messages(ids[0])
    assert next(row for row in rows if row.id == ids[2]).content == "original-answer-marker"
    assert not any(row.role == "assistant" and row.status == "complete" and row.id != ids[2] for row in rows)


def test_quota_rejection_does_not_create_request(regeneration):
    client, holder, model, ids = regeneration
    with Session(engine) as session:
        tenant = session.get(Tenant, holder["user"].tenant_id)
        tenant.message_limit = 1
        session.add(tenant)
        session.commit()
    assert client.post(_url(ids)).status_code == 429
    assert not model.requests and len(_messages(ids[0])) == 2


def test_corrupt_attachment_rejected_before_generation(regeneration):
    client, _holder, model, ids = regeneration
    with Session(engine) as session:
        row = session.get(Message, ids[1])
        row.attachments = '{"invalid":true}'
        session.add(row)
        session.commit()
    assert client.post(_url(ids)).status_code == 400
    assert not model.requests and len(_messages(ids[0])) == 2


def test_revoked_identity_cannot_save_successful_regeneration(regeneration):
    client, holder, model, ids = regeneration
    model.revoke = holder["user"].id
    response = client.post(_url(ids))
    assert response.status_code == 403
    rows = _messages(ids[0])
    assert [row.content for row in rows if row.id == ids[2]] == ["original-answer-marker"]
    assert not any(row.role == "assistant" and row.status == "complete" and row.id != ids[2] for row in rows)


@pytest.mark.parametrize("identity", ["NaN", "Infinity", "0", "x" * 129])
def test_extreme_message_identifiers_are_safe_not_found(regeneration, identity):
    client, _holder, model, ids = regeneration
    assert client.post(_url(ids, identity)).status_code == 404
    assert not model.requests and len(_messages(ids[0])) == 2


def test_regenerate_contract_is_in_openapi():
    operation = app.openapi()["paths"][
        "/api/v1/chat/conversations/{conversation_id}/messages/{message_id}/regenerate"]["post"]
    assert "requestBody" not in operation
    assert {"200", "403", "404", "409", "429", "502"} <= set(operation["responses"])
