"""HTTP contracts for human document permission changes."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.deps import get_current_user, get_db
from app.api.routes.rag import router
from app.models.rag import Document, DocumentACL
from app.models.user import Tenant, User


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="t", name="tenant"))
        user = User(id="admin", tenant_id="t", username="admin", hashed_password="unused", role="tenant_admin")
        session.add(user)
        session.add(Document(id="doc", tenant_id="t", user_id="admin", title="private"))
        session.commit()
        application = FastAPI()
        application.include_router(router)
        application.dependency_overrides[get_db] = lambda: session
        application.dependency_overrides[get_current_user] = lambda: user
        with TestClient(application) as client:
            yield client, session, user
    engine.dispose()


def test_propose_then_same_human_confirm_and_replay_rejected(api):
    client, session, user = api
    response = client.post("/rag/documents/doc/acl-proposals", json={"expected_revision": 0, "visibility": "private"})
    assert response.status_code == 201
    proposal = response.json()
    assert session.get(DocumentACL, "doc") is None
    path = f"/rag/acl-proposals/{proposal['id']}/confirm"
    body = {"reviewed_digest": proposal["reviewed_digest"]}
    assert client.post(path, json=body).json() == {"revision": 1}
    assert client.post(path, json=body).status_code == 409
    session.expire_all()
    assert session.get(DocumentACL, "doc").visibility == "private"


def test_member_cannot_propose_or_confirm(api):
    client, session, user = api
    user.role = "member"
    session.commit()
    response = client.post("/rag/documents/doc/acl-proposals", json={"expected_revision": 0, "visibility": "private"})
    assert response.status_code == 403
    assert client.post("/rag/acl-proposals/unknown/confirm", json={"reviewed_digest": "a" * 64}).status_code == 403


def test_approval_boolean_and_spoofed_subject_are_rejected(api):
    client, session, user = api
    body = {"expected_revision": 0, "visibility": "tenant", "approved": True, "tenant_id": "other"}
    assert client.post("/rag/documents/doc/acl-proposals", json=body).status_code == 422
    assert client.post("/rag/acl-proposals/unknown/confirm", json={"approved": True}).status_code == 422
