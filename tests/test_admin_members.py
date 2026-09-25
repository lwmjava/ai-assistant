"""系统管理员在指定租户下创建成员。"""

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.audit.models import AuditAction, AuditLog
from app.core.database import engine
from app.core.security import Role, decode_token, hash_password
from app.main import app
from app.models.conversation import Conversation
from app.models.rag import Document
from app.models.user import Tenant, User

PASSWORD = "Member@Pass1"


def setup_function() -> None:
    import app.models.conversation  # noqa: F401
    import app.models.rag  # noqa: F401
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


def _tenant(client: TestClient, headers: dict[str, str], name: str) -> str:
    resp = client.post("/api/admin/tenants", headers=headers, json={"name": name})
    assert resp.status_code == 201
    return resp.json()["id"]


def test_member_login_stays_in_tenant_and_audit_hides_password() -> None:
    client = _client()
    headers = _admin(client)
    tenant_id = _tenant(client, headers, "Acme")
    other_id = _tenant(client, headers, "Other")

    created = client.post(
        f"/api/admin/tenants/{tenant_id}/users",
        headers=headers,
        json={"username": "ada", "password": PASSWORD, "email": "ada@example.com", "role": "system_admin"},
    )
    assert created.status_code == 422

    created = client.post(
        f"/api/admin/tenants/{tenant_id}/users",
        headers=headers,
        json={"username": " ada ", "password": PASSWORD, "email": "ada@example.com"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["username"] == "ada"
    assert body["tenant_id"] == tenant_id
    assert body["role"] == "member"
    assert "password" not in body
    assert PASSWORD not in created.text

    with Session(engine) as session:
        user = session.get(User, body["id"])
        assert user is not None
        assert PASSWORD not in (user.hashed_password or "")
        logs = session.exec(
            select(AuditLog).where(AuditLog.action == AuditAction.USER_CREATE.value)
        ).all()
        matched = [log for log in logs if log.resource_id == body["id"]]
        assert matched
        assert PASSWORD not in (matched[0].details or "")
        assert user.hashed_password not in (matched[0].details or "")
        session.add(Conversation(tenant_id=tenant_id, user_id=user.id, title="mine"))
        session.add(Conversation(tenant_id=other_id, user_id="someone-else", title="theirs"))
        session.add(Document(tenant_id=tenant_id, user_id=user.id, title="mine-doc"))
        session.add(Document(tenant_id=other_id, user_id="someone-else", title="their-doc"))
        session.commit()

    login = client.post("/api/auth/login", json={"username": "ada", "password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["access_token"]
    payload = decode_token(token)
    assert payload["tenant_id"] == tenant_id
    assert payload["role"] == "member"
    member_headers = {"Authorization": f"Bearer {token}"}

    conversations = client.get("/api/chat/conversations", headers=member_headers)
    assert conversations.status_code == 200
    titles = {item["title"] for item in conversations.json()}
    assert titles == {"mine"}

    documents = client.get("/api/rag/documents", headers=member_headers)
    assert documents.status_code == 200
    doc_titles = {item["title"] for item in documents.json()}
    assert doc_titles == {"mine-doc"}


def test_rejects_missing_inactive_and_duplicates() -> None:
    client = _client()
    headers = _admin(client)
    missing = client.post(
        "/api/admin/tenants/missing/users",
        headers=headers,
        json={"username": "ada", "password": PASSWORD},
    )
    assert missing.status_code == 404

    with Session(engine) as session:
        inactive = Tenant(name="Closed", is_active=False)
        session.add(inactive)
        session.commit()
        session.refresh(inactive)
        inactive_id = inactive.id
    closed = client.post(
        f"/api/admin/tenants/{inactive_id}/users",
        headers=headers,
        json={"username": "ada", "password": PASSWORD},
    )
    assert closed.status_code == 409

    tenant_id = _tenant(client, headers, "Acme")
    assert (
        client.post(
            f"/api/admin/tenants/{tenant_id}/users",
            headers=headers,
            json={"username": "ada", "password": PASSWORD, "email": "ada@example.com"},
        ).status_code
        == 201
    )
    again = client.post(
        f"/api/admin/tenants/{tenant_id}/users",
        headers=headers,
        json={"username": "ada", "password": PASSWORD},
    )
    assert again.status_code == 409
    email = client.post(
        f"/api/admin/tenants/{tenant_id}/users",
        headers=headers,
        json={"username": "bea", "password": PASSWORD, "email": "ada@example.com"},
    )
    assert email.status_code == 409


def test_non_admin_cannot_create_member() -> None:
    client = _client()
    headers = _admin(client)
    tenant_id = _tenant(client, headers, "Acme")
    with Session(engine) as session:
        home = Tenant(name="home-member")
        session.add(home)
        session.commit()
        session.refresh(home)
        session.add(
            User(
                tenant_id=home.id,
                username="member",
                hashed_password=hash_password("Test@Pass123!"),
                role=Role.MEMBER.value,
            )
        )
        session.commit()
    login = client.post(
        "/api/auth/login",
        json={"username": "member", "password": "Test@Pass123!"},
    )
    resp = client.post(
        f"/api/admin/tenants/{tenant_id}/users",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"username": "ada", "password": PASSWORD},
    )
    assert resp.status_code == 403
