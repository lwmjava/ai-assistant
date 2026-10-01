"""系统管理员修改租户名称并停用租户。"""

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

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


def _admin(client: TestClient) -> dict[str, str]:
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
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Test@Pass123!"},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_rename_and_reject_duplicate_active_name() -> None:
    client = _client()
    headers = _admin(client)
    first = client.post("/api/admin/tenants", headers=headers, json={"name": "Acme"})
    second = client.post("/api/admin/tenants", headers=headers, json={"name": "Other"})
    assert first.status_code == 201
    assert second.status_code == 201
    first_id = first.json()["id"]

    renamed = client.patch(
        f"/api/admin/tenants/{first_id}",
        headers=headers,
        json={"name": " Acme North "},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Acme North"

    clash = client.patch(
        f"/api/admin/tenants/{first_id}",
        headers=headers,
        json={"name": "Other"},
    )
    assert clash.status_code == 409
    assert clash.json()["detail"] == "租户名称已存在"


def test_deactivate_blocks_member_and_stays_visible_to_admin() -> None:
    client = _client()
    headers = _admin(client)
    created = client.post("/api/admin/tenants", headers=headers, json={"name": "Acme"})
    tenant_id = created.json()["id"]
    member = client.post(
        f"/api/admin/tenants/{tenant_id}/users",
        headers=headers,
        json={"username": "ada", "password": PASSWORD},
    )
    assert member.status_code == 201
    login = client.post("/api/auth/login", json={"username": "ada", "password": PASSWORD})
    assert login.status_code == 200
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    refresh = login.json()["refresh_token"]

    other = client.post("/api/admin/tenants", headers=headers, json={"name": "Parked"})
    other_id = other.json()["id"]
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == "ada")).one()
        session.add(Membership(user_id=user.id, tenant_id=other_id, role="member"))
        session.commit()
        version = user.token_version
        home = user.tenant_id

    stopped = client.post(f"/api/admin/tenants/{other_id}/deactivate", headers=headers)
    assert stopped.status_code == 200
    assert stopped.json()["is_active"] is False

    switched = client.post(
        "/api/auth/switch-tenant",
        headers=member_headers,
        json={"tenant_id": other_id},
    )
    assert switched.status_code == 409
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == "ada")).one()
        assert user.tenant_id == home
        assert user.token_version == version

    deactivated = client.post(f"/api/admin/tenants/{tenant_id}/deactivate", headers=headers)
    assert deactivated.status_code == 200
    assert client.get("/api/auth/me", headers=member_headers).status_code == 403
    assert client.post("/api/auth/refresh", json={"refresh_token": refresh}).status_code == 403

    hidden = client.get("/api/admin/tenants", headers=headers)
    assert all(row["id"] != tenant_id for row in hidden.json())
    visible = client.get("/api/admin/tenants?include_inactive=true", headers=headers)
    row = next(item for item in visible.json() if item["id"] == tenant_id)
    assert row["is_active"] is False
    assert row["name"] == "Acme"

    again = client.patch(
        f"/api/admin/tenants/{tenant_id}",
        headers=headers,
        json={"name": "Renamed"},
    )
    assert again.status_code == 409


def test_activate_rejects_duplicate_name_then_restores_login() -> None:
    client = _client()
    headers = _admin(client)
    created = client.post("/api/admin/tenants", headers=headers, json={"name": "Acme"})
    tenant_id = created.json()["id"]
    member = client.post(
        f"/api/admin/tenants/{tenant_id}/users",
        headers=headers,
        json={"username": "ada", "password": PASSWORD},
    )
    assert member.status_code == 201
    login = client.post("/api/auth/login", json={"username": "ada", "password": PASSWORD})
    refresh = login.json()["refresh_token"]

    stopped = client.post(f"/api/admin/tenants/{tenant_id}/deactivate", headers=headers)
    assert stopped.status_code == 200
    duplicate = client.post("/api/admin/tenants", headers=headers, json={"name": "Acme"})
    assert duplicate.status_code == 201

    clash = client.post(f"/api/admin/tenants/{tenant_id}/activate", headers=headers)
    assert clash.status_code == 409
    assert clash.json()["detail"] == "租户名称已存在"
    still = client.get("/api/admin/tenants?include_inactive=true", headers=headers)
    row = next(item for item in still.json() if item["id"] == tenant_id)
    assert row["is_active"] is False

    parked = client.post(
        f"/api/admin/tenants/{duplicate.json()['id']}/deactivate",
        headers=headers,
    )
    assert parked.status_code == 200
    restored = client.post(f"/api/admin/tenants/{tenant_id}/activate", headers=headers)
    assert restored.status_code == 200
    assert restored.json()["is_active"] is True
    again = client.post(f"/api/admin/tenants/{tenant_id}/activate", headers=headers)
    assert again.status_code == 409

    stale = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert stale.status_code == 401
    fresh = client.post("/api/auth/login", json={"username": "ada", "password": PASSWORD})
    assert fresh.status_code == 200
