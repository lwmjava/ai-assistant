"""ADM-006：租户设置页成员接口。"""

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.core.database import engine
from app.core.security import Role, hash_password
from app.main import app
from app.models.user import Tenant, User
from app.services.membership import ensure_membership

PASSWORD = "Adm006@Pass1"


def setup_function() -> None:
    import app.models.membership  # noqa: F401
    import app.models.user  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


def _client() -> TestClient:
    return TestClient(app)


def _login(role: Role, username: str) -> dict[str, str]:
    with Session(engine) as session:
        tenant = session.exec(select(Tenant)).first()
        if tenant is None:
            tenant = Tenant(name="home")
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
        ensure_membership(session, user)
    client = _client()
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_tenant_admin_lists_and_creates_member() -> None:
    client = _client()
    headers = _login(Role.TENANT_ADMIN, "boss")
    listed = client.get("/api/v1/tenant/members", headers=headers)
    assert listed.status_code == 200
    assert any(row["username"] == "boss" for row in listed.json())

    created = client.post(
        "/api/v1/tenant/members",
        headers=headers,
        json={"username": "newbie", "password": PASSWORD, "email": "newbie@example.com"},
    )
    assert created.status_code == 201
    assert created.json()["username"] == "newbie"
    assert created.json()["role"] == Role.MEMBER.value

    with Session(engine) as session:
        row = session.exec(select(User).where(User.username == "newbie")).one()
        assert row.email == "newbie@example.com"


def test_member_cannot_create_tenant_member() -> None:
    client = _client()
    _login(Role.TENANT_ADMIN, "boss")
    headers = _login(Role.MEMBER, "ada")
    denied = client.post(
        "/api/v1/tenant/members",
        headers=headers,
        json={"username": "x", "password": PASSWORD},
    )
    assert denied.status_code == 403
