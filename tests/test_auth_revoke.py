"""系统管理员撤销刷新令牌。"""

import json

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.audit.models import AuditAction, AuditLog
from app.core.database import engine
from app.core.security import Role, hash_password
from app.main import app
from app.models.user import Tenant, User

PASSWORD = "Revoke@Pass1"


def setup_function() -> None:
    import app.models.user  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


def _client() -> TestClient:
    return TestClient(app)


def _user(username: str, role: Role, tenant_name: str) -> str:
    with Session(engine) as session:
        tenant = session.exec(select(Tenant).where(Tenant.name == tenant_name)).first()
        if tenant is None:
            tenant = Tenant(name=tenant_name)
            session.add(tenant)
            session.commit()
            session.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            username=username,
            hashed_password=hash_password(PASSWORD),
            role=role.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.id


def _login(client: TestClient, username: str) -> dict:
    resp = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200
    return resp.json()


def test_admin_revoke_invalidates_old_refresh_and_allows_new_login() -> None:
    client = _client()
    admin_id = _user("root", Role.SYSTEM_ADMIN, "default")
    member_id = _user("ada", Role.MEMBER, "default")
    admin = _login(client, "root")
    member = _login(client, "ada")

    revoked = client.post(
        f"/api/auth/users/{member_id}/revoke-tokens",
        headers={"Authorization": f"Bearer {admin['access_token']}"},
    )
    assert revoked.status_code == 200
    body = revoked.json()
    assert body["user_id"] == member_id
    assert body["token_version"] == 1
    assert PASSWORD not in revoked.text

    stale = client.post("/api/auth/refresh", json={"refresh_token": member["refresh_token"]})
    assert stale.status_code == 401

    again = _login(client, "ada")
    refreshed = client.post("/api/auth/refresh", json={"refresh_token": again["refresh_token"]})
    assert refreshed.status_code == 200

    with Session(engine) as session:
        logs = session.exec(
            select(AuditLog).where(
                AuditLog.action == AuditAction.USER_UPDATE.value,
                AuditLog.resource_id == member_id,
            )
        ).all()
        assert len(logs) == 1
        assert logs[0].user_id == admin_id
        raw_details = logs[0].details
        details = json.loads(raw_details) if isinstance(raw_details, str) else raw_details
        assert details["action"] == "revoke_tokens"
        assert PASSWORD not in str(raw_details)


def test_member_cannot_revoke_tokens() -> None:
    client = _client()
    _user("root", Role.SYSTEM_ADMIN, "default")
    member_id = _user("ada", Role.MEMBER, "default")
    member = _login(client, "ada")
    resp = client.post(
        f"/api/auth/users/{member_id}/revoke-tokens",
        headers={"Authorization": f"Bearer {member['access_token']}"},
    )
    assert resp.status_code == 403


def test_revoke_missing_user_is_404() -> None:
    client = _client()
    _user("root", Role.SYSTEM_ADMIN, "default")
    admin = _login(client, "root")
    resp = client.post(
        "/api/auth/users/missing-user/revoke-tokens",
        headers={"Authorization": f"Bearer {admin['access_token']}"},
    )
    assert resp.status_code == 404
