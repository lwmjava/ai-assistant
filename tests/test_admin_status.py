"""系统管理员查看数据库和向量库是否连通。"""

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.core.database import engine
from app.core.security import Role, hash_password
from app.main import app
from app.models.user import Tenant, User


def setup_function() -> None:
    import app.models.user  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


def _client() -> TestClient:
    return TestClient(app)


def _login(client: TestClient, role: str, username: str) -> dict[str, str]:
    with Session(engine) as session:
        tenant = session.exec(select(Tenant)).first()
        if tenant is None:
            tenant = Tenant(name="home")
            session.add(tenant)
            session.commit()
            session.refresh(tenant)
        session.add(
            User(
                tenant_id=tenant.id,
                username=username,
                hashed_password=hash_password("Test@Pass123!"),
                role=role,
            )
        )
        session.commit()
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": "Test@Pass123!"},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_admin_sees_connectivity_without_invented_counts() -> None:
    client = _client()
    headers = _login(client, Role.SYSTEM_ADMIN.value, "admin")
    resp = client.get("/api/admin/system/status", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["checks"]["database"]["status"] in {"ok", "error"}
    assert body["checks"]["vector_store"]["backend"]
    assert body["version"]
    assert body["started_at"]
    assert "online_users" not in body
    assert "request_count" not in body


def test_member_cannot_open_system_status() -> None:
    client = _client()
    _login(client, Role.SYSTEM_ADMIN.value, "admin")
    headers = _login(client, Role.MEMBER.value, "ada")
    resp = client.get("/api/admin/system/status", headers=headers)
    assert resp.status_code == 403
