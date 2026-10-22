"""Feedback HTTP contracts with real user and tenant dependencies."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.routes import chat as chat_routes
from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.models.conversation import Conversation, Message
from app.models.feedback import ChatAttempt
from app.models.user import Tenant, User
from app.services.chat_feedback import FeedbackService


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="feedback-a", name="Feedback"))
        session.add(User(id="feedback-owner", tenant_id="feedback-a", username="owner", hashed_password=""))
        session.add(
            User(id="feedback-admin", tenant_id="feedback-a", username="admin", hashed_password="", role="system_admin")
        )
        session.commit()

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = database
    calls = []

    async def chat(session, user, message, conversation_id, **kwargs):
        calls.append(message)
        conv = Conversation(tenant_id=user.tenant_id, user_id=user.id)
        session.add(conv)
        session.commit()
        question = Message(conversation_id=conv.id, role="user", content=message)
        session.add(question)
        session.commit()
        session.info["agent_message_id"] = question.id
        session.info["feedback_message_ready"].set()
        if message == "fail":
            raise RuntimeError("private-internal-credential")
        answer = Message(conversation_id=conv.id, role="assistant", content="synthetic answer")
        session.add(answer)
        session.commit()
        session.info["agent_assistant_message_id"] = answer.id
        return conv, answer.content

    monkeypatch.setattr(chat_routes, "_service", SimpleNamespace(chat=chat, llm=SimpleNamespace(model="synthetic")))
    headers = {"Authorization": "Bearer " + create_access_token("feedback-owner", "feedback-a", "member")}
    admin = {"Authorization": "Bearer " + create_access_token("feedback-admin", "feedback-a", "system_admin")}
    yield TestClient(app), engine, headers, admin, calls
    app.dependency_overrides.pop(get_session, None)
    engine.dispose()


def test_legacy_chat_generates_durable_id_and_success_rating(context):
    client, _, headers, _, _ = context
    response = client.post("/api/v1/chat", headers=headers, json={"message": "hello"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["attempt_id"]) == 32 and payload["assistant_message_id"]
    attempt = client.get("/api/v1/chat/attempts/" + payload["attempt_id"], headers=headers)
    assert attempt.json()["outcome"] == "success"
    rated = client.post(
        "/api/v1/chat/ratings",
        headers=headers,
        json={
            "assistant_message_id": payload["assistant_message_id"],
            "rating": "down",
            "comment": "联系13800138000 a@example.com",
            "reason_code": "citation",
        },
    )
    assert rated.status_code == 200, rated.text
    assert "13800138000" not in rated.text and "a@example.com" not in rated.text
    listing = client.get("/api/v1/chat/ratings", headers=headers).json()
    assert listing["total"] == 1 and listing["items"][0]["rating"] == "down"
    assert client.delete("/api/v1/chat/ratings/" + rated.json()["id"], headers=headers).status_code == 200


def test_failure_response_has_safe_ids_and_feedback_is_private(context):
    client, engine, headers, admin, _ = context
    response = client.post("/api/v1/chat", headers=headers, json={"message": "fail"})
    assert response.status_code == 500 and "private-internal-credential" not in response.text
    payload = response.json()
    assert payload["attempt_id"] and payload["conversation_id"] and payload["user_message_id"]
    body = {"attempt_id": payload["attempt_id"], "kind": "api_error", "comment": "电话13800138000"}
    denied = client.post("/api/v1/chat/feedback", headers=admin, json=body)
    assert denied.status_code == 404
    saved = client.post("/api/v1/chat/feedback", headers=headers, json=body)
    assert saved.status_code == 200 and "13800138000" not in saved.text
    duplicate = client.post("/api/v1/chat/feedback", headers=headers, json={**body, "comment": "changed"})
    assert saved.json()["id"] == duplicate.json()["id"]
    assert duplicate.json()["comment"] == saved.json()["comment"]
    state = client.get("/api/v1/chat/attempts/" + payload["attempt_id"], headers=headers).json()
    assert state["feedback_submitted"] and state["feedback_id"] == saved.json()["id"]
    with Session(engine) as session:
        conv = session.get(Conversation, payload["conversation_id"])
        user = session.get(User, "feedback-owner")
        metadata = FeedbackService(session).message_metadata(user, conv)
        assert metadata[payload["user_message_id"]]["feedback_submitted"]
        assert "comment" not in str(metadata)


def test_duplicate_run_rejected_without_second_execution(context):
    client, _, headers, _, calls = context
    body = {"message": "hello", "run_id": str(uuid.uuid4())}
    assert client.post("/api/v1/chat", headers=headers, json=body).status_code == 200
    assert client.post("/api/v1/chat", headers=headers, json=body).status_code == 409
    assert calls == ["hello"]


def test_validation_never_echoes_unredacted_comment(context):
    client, _, headers, _, _ = context
    secret = "private-comment-" * 50
    for body in [
        {"attempt_id": "not-an-id", "kind": "api_error", "comment": secret},
        {"attempt_id": uuid.uuid4().hex, "kind": "invented", "comment": "secret-token-value"},
    ]:
        response = client.post("/api/v1/chat/feedback", headers=headers, json=body)
        assert response.status_code == 422
        assert body["comment"] not in response.text
    assert client.get("/api/v1/chat/feedback?page_size=51", headers=headers).status_code == 422


def test_unauthenticated_cannot_access_feedback(context):
    client, _, _, _, _ = context
    for path in ["feedback", "ratings", "attempts/" + uuid.uuid4().hex]:
        assert client.get("/api/v1/chat/" + path).status_code == 401


def test_deleted_or_stopped_source_rejects_submission(context):
    client, engine, headers, _, _ = context
    response = client.post("/api/v1/chat", headers=headers, json={"message": "hello"}).json()
    with Session(engine) as session:
        row = session.get(ChatAttempt, response["attempt_id"])
        row.outcome = "stopped"
        session.add(row)
        session.commit()
    assert (
        client.post(
            "/api/v1/chat/feedback", headers=headers, json={"attempt_id": response["attempt_id"], "kind": "api_error"}
        ).status_code
        == 422
    )
