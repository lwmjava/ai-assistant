"""Public authentication errors through the HTTP boundary with isolated identities."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.database import engine
from app.core.security import Role, create_access_token, create_refresh_token, hash_password
from app.main import app
from app.models.user import Tenant, User


@pytest.fixture
def identity():
    with Session(engine) as session:
        tenant = Tenant(name=f"errors-{uuid4().hex}")
        session.add(tenant)
        session.flush()
        user = User(tenant_id=tenant.id, username=f"errors-{uuid4().hex}",
                    hashed_password=hash_password("Synthetic-pass-123"), role=Role.MEMBER.value)
        session.add(user)
        session.commit()
        session.refresh(user)
        yield user.id, tenant.id, user.username


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert error["retry_after"] is None
    assert error["request_id"] == response.headers["x-request-id"]
    assert "Traceback" not in response.text


def test_expired_refresh_has_stable_public_error(identity):
    uid, _, _ = identity
    import jwt

    from app.core.security import JWT_ALGORITHM, JWT_SECRET_KEY

    token = jwt.encode({"sub": uid, "type": "refresh", "tv": 0,
                        "exp": datetime.now(UTC) - timedelta(seconds=5)},
                       JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": token})
    assert_error(response, 401, "session_expired")
    assert token not in response.text


def test_revoked_refresh_is_distinguished_from_expiry(identity):
    uid, _, _ = identity
    token = create_refresh_token(uid, 0)
    with Session(engine) as session:
        user = session.get(User, uid)
        user.token_version = 1
        session.add(user)
        session.commit()
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": token})
    assert_error(response, 401, "token_revoked")


def test_disabled_user_login_does_not_disclose_status_for_wrong_password(identity):
    uid, _, name = identity
    with Session(engine) as session:
        user = session.get(User, uid)
        user.is_active = False
        session.add(user)
        session.commit()
    with TestClient(app) as client:
        wrong = client.post("/api/v1/auth/login", json={"username": name, "password": "wrong"})
        correct = client.post("/api/v1/auth/login", json={"username": name, "password": "Synthetic-pass-123"})
        token = create_access_token(uid, identity[1], Role.MEMBER.value)
        access = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert wrong.status_code == 401
    assert "user_disabled" not in wrong.text
    assert_error(correct, 403, "user_disabled")
    assert_error(access, 403, "user_disabled")


def test_suspended_tenant_error_and_existing_admin_exception(identity):
    uid, tid, _ = identity
    with Session(engine) as session:
        tenant = session.get(Tenant, tid)
        tenant.is_active = False
        session.add(tenant)
        session.commit()
    token = create_access_token(uid, tid, Role.MEMBER.value)
    with TestClient(app) as client:
        denied = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert_error(denied, 403, "tenant_suspended")
        with Session(engine) as session:
            user = session.get(User, uid)
            user.role = Role.SYSTEM_ADMIN.value
            session.add(user)
            session.commit()
        admin = create_access_token(uid, tid, Role.SYSTEM_ADMIN.value)
        assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin}"}).status_code == 200


def test_successful_refresh_remains_success_without_error_envelope(identity):
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": create_refresh_token(identity[0])})
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert "error" not in response.json()


def test_upload_limit_rejects_before_document_creation(identity, monkeypatch):
    from sqlmodel import select

    from app.core.config import settings
    from app.models.rag import Document

    monkeypatch.setattr(settings, "RAG_UPLOAD_MAX_BYTES", 8)
    token = create_access_token(identity[0], identity[1], Role.MEMBER.value)
    with TestClient(app) as client:
        response = client.post("/api/v1/rag/documents/upload",
                               files={"file": ("example.txt", b"x" * 9, "text/plain")},
                               headers={"Authorization": f"Bearer {token}"})
    assert_error(response, 413, "file_too_large")
    assert response.json()["limit_bytes"] == 8
    assert "8" in response.json()["error"]["message"]
    with Session(engine) as session:
        assert not session.exec(select(Document).where(Document.tenant_id == identity[1])).all()


@pytest.mark.parametrize("value, expected", [
    (None, None), (False, None), (0, None), (-1, None),
    (float("nan"), None), (float("inf"), None), (10**30, None),
    (1.1, 2), (86400, 86400), (86401, None), ("5", None),
])
def test_countdown_rejects_unbounded_values(value, expected):
    from app.api.errors import retry_seconds

    assert retry_seconds(value) == expected


@pytest.mark.parametrize("stream", [False, True])
def test_model_failure_uses_real_service_and_safe_public_boundary(identity, monkeypatch, stream):
    import json

    from sqlmodel import col, select

    from app.api.routes import chat as route
    from app.models.conversation import Conversation, Message
    from app.services.chat_service import ChatService
    from tests.test_error_model_boundary import UnavailableModel

    service = ChatService(UnavailableModel())
    reflections = []
    monkeypatch.setattr(service, "_maybe_reflect", lambda *args: reflections.append(args))
    monkeypatch.setattr(route, "_service", service)
    token = create_access_token(identity[0], identity[1], Role.MEMBER.value)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/stream" if stream else "/api/v1/chat",
                               json={"message": "你好"}, headers={"Authorization": f"Bearer {token}"})
    if stream:
        assert response.status_code == 200
        records = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        errors = [row for row in records if row.get("code") == "model_unavailable"]
        assert len(errors) == 1
        assert errors[0]["error"]["code"] == "model_unavailable"
        assert errors[0]["error"]["request_id"] == response.headers["x-request-id"]
        assert errors[0]["error"]["message"] == "模型服务暂不可用，请联系管理员检查模型配置"
    else:
        assert_error(response, 502, "model_unavailable")
    with Session(engine) as session:
        conv = session.exec(select(Conversation).where(col(Conversation.user_id) == identity[0])).one()
        rows = session.exec(select(Message).where(col(Message.conversation_id) == conv.id)).all()
        assert len([row for row in rows if row.role == "user"]) == 1
        assistants = [row for row in rows if row.role == "assistant"]
        assert len(assistants) == 1
        assert assistants[0].status == "failed"
    assert reflections == []
