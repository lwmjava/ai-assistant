"""公开注册加入 default 租户，以及一次性初始化向导。"""

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.audit.models import AuditAction, AuditLog
from app.core.database import engine
from app.core.security import Role, decode_token, hash_password
from app.main import app
from app.models.user import Tenant, User

PASSWORD = "Register@Pass1"


def setup_function() -> None:
    import app.models.user  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


def _client() -> TestClient:
    return TestClient(app)


def test_register_joins_default_tenant_and_hides_password() -> None:
    client = _client()
    created = client.post(
        "/api/auth/register",
        json={"username": " ada ", "password": PASSWORD, "email": "ada@example.com", "role": "system_admin"},
    )
    assert created.status_code == 422

    created = client.post(
        "/api/auth/register",
        json={"username": " ada ", "password": PASSWORD, "email": "ada@example.com"},
    )
    assert created.status_code == 201
    body = created.json()
    assert set(body) == {"access_token", "refresh_token", "token_type"}
    assert PASSWORD not in created.text
    payload = decode_token(body["access_token"])
    assert payload["role"] == Role.MEMBER.value

    with Session(engine) as session:
        tenant = session.exec(select(Tenant).where(Tenant.name == "default")).one()
        user = session.exec(select(User).where(User.username == "ada")).one()
        assert user.tenant_id == tenant.id
        assert user.role == Role.MEMBER.value
        assert payload["tenant_id"] == tenant.id
        logs = session.exec(
            select(AuditLog).where(AuditLog.action == AuditAction.USER_CREATE.value)
        ).all()
        assert len(logs) == 1
        assert PASSWORD not in str(logs[0].details)

    login = client.post("/api/auth/login", json={"username": "ada", "password": PASSWORD})
    assert login.status_code == 200
    assert decode_token(login.json()["access_token"])["tenant_id"] == payload["tenant_id"]


def test_register_rejects_duplicate_username() -> None:
    client = _client()
    first = client.post("/api/auth/register", json={"username": "ada", "password": PASSWORD})
    assert first.status_code == 201
    again = client.post("/api/auth/register", json={"username": "ada", "password": PASSWORD})
    assert again.status_code == 409
    assert again.json()["detail"] == "用户名已存在"


def test_setup_is_once_and_register_still_joins_default() -> None:
    client = _client()
    status = client.get("/api/auth/setup-status")
    assert status.status_code == 200
    assert status.json() == {"needs_setup": True}

    created = client.post(
        "/api/auth/setup",
        json={"username": "root", "password": PASSWORD, "email": "root@example.com"},
    )
    assert created.status_code == 201
    assert PASSWORD not in created.text
    admin = decode_token(created.json()["access_token"])
    assert admin["role"] == Role.SYSTEM_ADMIN.value

    closed = client.post("/api/auth/setup", json={"username": "other", "password": PASSWORD})
    assert closed.status_code == 404
    assert client.get("/api/auth/setup-status").json() == {"needs_setup": False}

    member = client.post("/api/auth/register", json={"username": "ada", "password": PASSWORD})
    assert member.status_code == 201
    assert decode_token(member.json()["access_token"])["tenant_id"] == admin["tenant_id"]
    assert decode_token(member.json()["access_token"])["role"] == Role.MEMBER.value


def test_setup_closed_when_admin_already_exists() -> None:
    with Session(engine) as session:
        tenant = Tenant(name="other")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        session.add(
            User(
                tenant_id=tenant.id,
                username="root",
                hashed_password=hash_password(PASSWORD),
                role=Role.SYSTEM_ADMIN.value,
            )
        )
        session.commit()

    client = _client()
    assert client.get("/api/auth/setup-status").json() == {"needs_setup": False}
    closed = client.post("/api/auth/setup", json={"username": "another", "password": PASSWORD})
    assert closed.status_code == 404
