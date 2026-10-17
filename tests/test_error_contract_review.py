"""Independent HTTP-entry counterexamples for the nine PRD error scenarios.

These use persisted synthetic identities and real authorization/quota/limit code;
they do not replace route dependencies or read host credentials.
"""

import json
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import engine
from app.core.security import Role, create_access_token, create_refresh_token, hash_password
from app.main import app
from app.models.conversation import Conversation, Message
from app.models.rag import Document, ImportJob
from app.models.user import Tenant, User

PASSWORD = "Review-synthetic-password-123"
PUBLIC_CODES = {
    "session_expired", "token_revoked", "user_disabled", "tenant_suspended",
    "rate_limited", "quota_exceeded", "model_unavailable", "file_too_large",
}


@pytest.fixture
def make_identity():
    def create(role=Role.MEMBER, **tenant_options):
        with Session(engine) as session:
            tenant = Tenant(name=f"err-review-{uuid4().hex}", **tenant_options)
            session.add(tenant)
            session.flush()
            user = User(tenant_id=tenant.id, username=f"err-review-{uuid4().hex}",
                        hashed_password=hash_password(PASSWORD), role=role.value)
            session.add(user)
            session.commit()
            session.refresh(user)
            session.expunge(user)
            return user

    return create


def headers(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.tenant_id, user.role)}"}


def public_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert set(error) == {"code", "message", "retry_after", "request_id"}
    assert error["message"]
    assert error["request_id"] == response.headers["x-request-id"]
    assert PASSWORD not in response.text
    assert "Traceback" not in response.text
    assert "TokenRevokedError" not in response.text
    return error


def tenant_state(tenant_id):
    """Read committed final state using a fresh session, not an ORM identity map."""
    with Session(engine) as session:
        conversations = session.exec(select(Conversation).where(col(Conversation.tenant_id) == tenant_id)).all()
        ids = [row.id for row in conversations]
        messages = session.exec(select(Message).where(col(Message.conversation_id).in_(ids))).all() if ids else []
        documents = session.exec(select(Document).where(col(Document.tenant_id) == tenant_id)).all()
        jobs = session.exec(select(ImportJob).where(col(ImportJob.tenant_id) == tenant_id)).all()
        return ([row.id for row in conversations], [row.id for row in messages],
                [row.id for row in documents], [row.id for row in jobs])


def test_review_admin_revoke_real_route_persists_and_new_login_recovers(make_identity):
    admin = make_identity(Role.SYSTEM_ADMIN)
    target = make_identity()
    old_refresh = create_refresh_token(target.id)
    with TestClient(app) as client:
        revoked = client.post(f"/api/v1/auth/users/{target.id}/revoke-tokens", headers=headers(admin))
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["user_id"] == target.id
        with Session(engine) as session:
            persisted = session.get(User, target.id)
            assert persisted is not None and persisted.token_version == 1
            assert persisted.tenant_id == target.tenant_id
        stale = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
        public_error(stale, 401, "token_revoked")
        assert old_refresh not in stale.text
        login = client.post("/api/v1/auth/login", json={"username": target.username, "password": PASSWORD})
        assert login.status_code == 200, login.text
        fresh = client.post("/api/v1/auth/refresh", json={"refresh_token": login.json()["refresh_token"]})
        assert fresh.status_code == 200 and "error" not in fresh.json()


@pytest.mark.parametrize("role", [Role.MEMBER, Role.TENANT_ADMIN])
def test_review_non_system_admin_cannot_revoke_other_tenant_user(make_identity, role):
    actor = make_identity(role)
    target = make_identity()
    with TestClient(app) as client:
        result = client.post(f"/api/v1/auth/users/{target.id}/revoke-tokens", headers=headers(actor))
    assert result.status_code == 403
    with Session(engine) as session:
        assert session.get(User, target.id).token_version == 0
        assert session.get(User, actor.id).token_version == 0


@pytest.mark.parametrize("state,code", [("user", "user_disabled"), ("tenant", "tenant_suspended")])
@pytest.mark.parametrize("entry", ["login", "refresh", "access", "chat", "stream"])
def test_review_disabled_identity_denied_at_each_public_entry(make_identity, state, code, entry):
    user = make_identity()
    access_headers = headers(user)
    refresh = create_refresh_token(user.id)
    before = tenant_state(user.tenant_id)
    with Session(engine) as session:
        row = session.get(User, user.id) if state == "user" else session.get(Tenant, user.tenant_id)
        row.is_active = False
        session.add(row)
        session.commit()
    with TestClient(app) as client:
        if entry == "login":
            response = client.post("/api/v1/auth/login", json={"username": user.username, "password": PASSWORD})
        elif entry == "refresh":
            response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        elif entry == "access":
            response = client.get("/api/v1/auth/me", headers=access_headers)
        else:
            route = "/api/v1/chat/stream" if entry == "stream" else "/api/v1/chat"
            response = client.post(route, json={"message": "synthetic refusal check"}, headers=access_headers)
    public_error(response, 403, code)
    assert tenant_state(user.tenant_id) == before


@pytest.mark.parametrize("route", ["/api/v1/chat", "/api/v1/chat/stream"])
def test_review_message_quota_rejection_does_not_create_any_rows(make_identity, route):
    user = make_identity(message_limit=0)
    before = tenant_state(user.tenant_id)
    with TestClient(app) as client:
        response = client.post(route, headers=headers(user), json={"message": "quota boundary"})
    public_error(response, 429, "quota_exceeded")
    assert response.json()["used"] == 0 and response.json()["limit"] == 0
    assert response.json()["limit_type"] == "messages"
    assert tenant_state(user.tenant_id) == before


@pytest.mark.parametrize("route", ["/api/v1/chat", "/api/v1/chat/stream"])
def test_review_real_exhausted_rate_bucket_rejects_without_rows(make_identity, monkeypatch, route):
    from app.security import get_rate_limiter, reset_security_singletons

    user = make_identity()
    monkeypatch.setattr(settings, "SECURITY_ENABLED", True)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT", True)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT_CAPACITY", 1.0)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT_RATE", 1.0)
    monkeypatch.setattr("app.security.rate_limiter.time.monotonic", lambda: 1_000_000.0)
    reset_security_singletons()
    try:
        assert get_rate_limiter().allow(f"{user.tenant_id}:{user.id}")[0]
        before = tenant_state(user.tenant_id)
        with TestClient(app) as client:
            response = client.post(route, headers=headers(user), json={"message": "rate boundary"})
        if route.endswith("/stream"):
            assert response.status_code == 200
            frames = []
            for frame in response.text.replace("\r\n", "\n").split("\n\n"):
                fields = dict(line.split(":", 1) for line in frame.splitlines() if ":" in line)
                if "event" in fields and "data" in fields:
                    frames.append((fields["event"].strip(), json.loads(fields["data"].strip())))
            errors = [payload for event, payload in frames if event == "error"]
            assert len(errors) == 1
            error = errors[0]["error"]
            assert error["code"] == "rate_limited"
            assert error["request_id"] == response.headers["x-request-id"]
            assert any(event == "run.finished" and payload["status"] == "failed" for event, payload in frames)
        else:
            error = public_error(response, 429, "rate_limited")
            assert response.headers["retry-after"] == "1"
        assert error["retry_after"] == 1
        assert tenant_state(user.tenant_id) == before
    finally:
        reset_security_singletons()


@pytest.mark.parametrize("reason", ["size", "quota"])
@pytest.mark.parametrize("batch", [False, True])
def test_review_upload_refusal_preserves_existing_files_and_database(
    make_identity, monkeypatch, tmp_path, reason, batch,
):
    import app.rag.document_storage as storage

    user = make_identity(**({"storage_limit_bytes": 8} if reason == "quota" else {}))
    monkeypatch.setattr(storage, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "RAG_UPLOAD_MAX_BYTES", 8 if reason == "size" else 32)
    existing = tmp_path / "existing.txt"
    existing.write_bytes(b"old-resource")
    before = tenant_state(user.tenant_id)
    files = [("files" if batch else "file", ("synthetic.txt", b"123456789", "text/plain"))]
    route = "/api/v1/rag/import-jobs/upload" if batch else "/api/v1/rag/documents/upload"
    with TestClient(app) as client:
        response = client.post(route, headers=headers(user), files=files)
    code = "file_too_large" if reason == "size" else "quota_exceeded"
    public_error(response, 413 if reason == "size" else 429, code)
    if reason == "size":
        assert response.json()["limit_bytes"] == 8
        assert "8" in response.json()["error"]["message"]
    assert tenant_state(user.tenant_id) == before
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [existing]
    assert existing.read_bytes() == b"old-resource"


@pytest.mark.parametrize("supplied", [
    "Bearer synthetic-sensitive-token", "password=synthetic-sensitive-value", "x" * 1025,
])
def test_review_sensitive_request_id_is_not_reflected_in_public_error(make_identity, supplied):
    user = make_identity()
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": "invalid-synthetic-token"},
                               headers={"X-Request-ID": supplied})
    error = public_error(response, 401, "session_expired")
    assert supplied not in response.text
    assert supplied not in response.headers["x-request-id"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,128}", error["request_id"])
    assert tenant_state(user.tenant_id) == ([], [], [], [])


def test_review_openapi_codes_have_enum_and_endpoint_response_examples():
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    document = response.json()
    schemas = document["components"]["schemas"]
    enums = [set(row.get("properties", {}).get("code", {}).get("enum", [])) for row in schemas.values()]
    assert PUBLIC_CODES in enums
    required_examples = {
        ("/api/v1/auth/refresh", "401"): {"session_expired", "token_revoked"},
        ("/api/v1/auth/login", "403"): {"user_disabled", "tenant_suspended"},
        ("/api/v1/chat", "429"): {"rate_limited", "quota_exceeded"},
        ("/api/v1/chat", "502"): {"model_unavailable"},
        ("/api/v1/rag/documents/upload", "413"): {"file_too_large"},
    }
    for (path, status), codes in required_examples.items():
        content = document["paths"][path]["post"]["responses"][status]["content"]["application/json"]
        examples = content["examples"]
        actual = {row["value"]["error"]["code"] for row in examples.values()}
        assert codes <= actual, (path, status, actual)
        for row in examples.values():
            assert set(row["value"]["error"]) == {"code", "message", "retry_after", "request_id"}
    assert "Traceback" not in json.dumps(document, ensure_ascii=False)
