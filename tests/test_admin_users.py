"""系统管理员列出用户、修改角色和停用用户。"""

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.audit.models import AuditAction, AuditLog
from app.core.database import engine
from app.core.security import Role, hash_password
from app.main import app
from app.models.membership import Membership
from app.models.user import Tenant, User

PASSWORD = "Member@Pass1"


def setup_function() -> None:
    import app.models.user  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


def _client() -> TestClient:
    return TestClient(app)


def _admin(client: TestClient) -> tuple[dict[str, str], str]:
    with Session(engine) as session:
        tenant = Tenant(name="home-admin")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        session.add(
            User(
                tenant_id=tenant.id,
                username="admin",
                hashed_password=hash_password("Test@Pass123!"),
                role=Role.SYSTEM_ADMIN.value,
            )
        )
        session.commit()
        admin_id = session.exec(select(User).where(User.username == "admin")).one().id
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Test@Pass123!"},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}, admin_id


def _member(client: TestClient, headers: dict[str, str]) -> tuple[str, dict[str, str], str]:
    created_tenant = client.post("/api/admin/tenants", headers=headers, json={"name": "Acme"})
    assert created_tenant.status_code == 201
    tenant_id = created_tenant.json()["id"]
    created = client.post(
        f"/api/admin/tenants/{tenant_id}/users",
        headers=headers,
        json={"username": "ada", "password": PASSWORD, "email": "ada@example.com"},
    )
    assert created.status_code == 201
    user_id = created.json()["id"]
    login = client.post("/api/auth/login", json={"username": "ada", "password": PASSWORD})
    assert login.status_code == 200
    body = login.json()
    member_headers = {"Authorization": f"Bearer {body['access_token']}"}
    return user_id, member_headers, body["refresh_token"]


def test_non_admin_cannot_list_users() -> None:
    client = _client()
    headers, _ = _admin(client)
    _, member_headers, _ = _member(client, headers)
    resp = client.get("/api/admin/users", headers=member_headers)
    assert resp.status_code == 403


def test_list_shows_ids_and_role_change_is_visible_on_same_token() -> None:
    client = _client()
    headers, admin_id = _admin(client)
    user_id, member_headers, _ = _member(client, headers)

    listed = client.get("/api/admin/users", headers=headers)
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] >= 2
    ada = next(item for item in body["items"] if item["username"] == "ada")
    assert ada["id"] == user_id
    assert ada["tenant_name"] == "Acme"
    assert ada["tenant_id"]

    changed = client.patch(
        f"/api/admin/users/{user_id}",
        headers=headers,
        json={"role": "tenant_admin"},
    )
    assert changed.status_code == 200
    assert changed.json()["role"] == "tenant_admin"

    me = client.get("/api/auth/me", headers=member_headers)
    assert me.status_code == 200
    assert me.json()["role"] == "tenant_admin"

    with Session(engine) as session:
        row = session.exec(
            select(Membership).where(Membership.user_id == user_id)
        ).one()
        assert row.role == "tenant_admin"
        log = session.exec(
            select(AuditLog).where(AuditLog.action == AuditAction.USER_ROLE_CHANGE.value)
        ).one()
        assert "password" not in (log.details or "")

    own = client.patch(
        f"/api/admin/users/{admin_id}",
        headers=headers,
        json={"role": "viewer"},
    )
    assert own.status_code == 409
    assert own.json()["detail"] == "不能修改自己的角色"


def test_disable_rejects_self_and_blocks_old_tokens() -> None:
    client = _client()
    headers, admin_id = _admin(client)
    user_id, member_headers, refresh = _member(client, headers)

    self_disable = client.post(f"/api/admin/users/{admin_id}/disable", headers=headers)
    assert self_disable.status_code == 409

    disabled = client.post(f"/api/admin/users/{user_id}/disable", headers=headers)
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False

    assert client.get("/api/auth/me", headers=member_headers).status_code == 401
    refreshed = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert refreshed.status_code == 401

    again = client.post(f"/api/admin/users/{user_id}/disable", headers=headers)
    assert again.status_code == 409
