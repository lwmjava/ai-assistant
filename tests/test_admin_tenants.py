"""系统管理员创建并列出租户。"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.audit.models import AuditAction, AuditLog
from app.core.database import engine
from app.core.security import Role, hash_password
from app.main import app
from app.models.user import Tenant, User


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    import app.models.user  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _user(role: Role, username: str) -> User:
    with Session(engine) as session:
        tenant = Tenant(name=f"home-{username}")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            username=username,
            hashed_password=hash_password("Test@Pass123!"),
            role=role.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _headers(client: TestClient, username: str) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": "Test@Pass123!"},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_non_admin_cannot_create_or_list(client: TestClient) -> None:
    for role, username in (
        (Role.TENANT_ADMIN, "tenant_admin"),
        (Role.MEMBER, "member"),
        (Role.SYSTEM_VIEWER, "viewer"),
    ):
        _user(role, username)
        headers = _headers(client, username)
        assert client.get("/api/admin/tenants", headers=headers).status_code == 403
        created = client.post(
            "/api/admin/tenants",
            headers=headers,
            json={"name": f"acme-{username}"},
        )
        assert created.status_code == 403


def test_admin_creates_lists_and_audits(client: TestClient) -> None:
    _user(Role.SYSTEM_ADMIN, "admin")
    headers = _headers(client, "admin")

    created = client.post("/api/admin/tenants", headers=headers, json={"name": "  Acme  "})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Acme"
    assert body["is_active"] is True
    assert "users" not in body

    listed = client.get("/api/admin/tenants", headers=headers)
    assert listed.status_code == 200
    names = {item["name"] for item in listed.json()}
    assert "Acme" in names

    with Session(engine) as session:
        logs = session.exec(
            select(AuditLog).where(AuditLog.action == AuditAction.TENANT_CREATE.value)
        ).all()
    assert any(log.resource_id == body["id"] for log in logs)


def test_duplicate_active_name_rejected(client: TestClient) -> None:
    _user(Role.SYSTEM_ADMIN, "admin")
    headers = _headers(client, "admin")
    assert client.post("/api/admin/tenants", headers=headers, json={"name": "Acme"}).status_code == 201
    again = client.post("/api/admin/tenants", headers=headers, json={"name": "Acme"})
    assert again.status_code == 409


def test_inactive_same_name_can_be_created_and_hidden_from_list(client: TestClient) -> None:
    _user(Role.SYSTEM_ADMIN, "admin")
    headers = _headers(client, "admin")
    with Session(engine) as session:
        session.add(Tenant(name="Acme", is_active=False))
        session.commit()

    created = client.post("/api/admin/tenants", headers=headers, json={"name": "Acme"})
    assert created.status_code == 201

    listed = client.get("/api/admin/tenants", headers=headers)
    active = [item for item in listed.json() if item["name"] == "Acme"]
    assert len(active) == 1
    assert active[0]["is_active"] is True
