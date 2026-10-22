"""AUTH-005：对话读权限与 §5.3.2 矩阵一致。"""

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.core.database import engine
from app.core.security import Role, check_permission, hash_password
from app.main import app
from app.models.user import Tenant, User
from app.services.membership import ensure_membership

PASSWORD = "Auth005@Pass1"


def setup_function() -> None:
    import app.models.membership  # noqa: F401
    import app.models.user  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


def _client() -> TestClient:
    return TestClient(app)


def _seed(role: Role, username: str) -> dict[str, str]:
    with Session(engine) as session:
        tenant = Tenant(name=f"t-{username}")
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
    login = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_matrix_denies_viewer_and_system_viewer_conversation_read() -> None:
    assert check_permission(Role.VIEWER.value, "conversations", "read") is False
    assert check_permission(Role.SYSTEM_VIEWER.value, "conversations", "read") is False
    assert check_permission(Role.MEMBER.value, "conversations", "read") is True
    assert check_permission(Role.TENANT_ADMIN.value, "conversations", "read") is True
    assert check_permission(Role.SYSTEM_ADMIN.value, "conversations", "read") is True


def test_viewer_and_system_viewer_list_conversations_forbidden() -> None:
    client = _client()
    for role, name in (
        (Role.VIEWER, "viewer-ada"),
        (Role.SYSTEM_VIEWER, "sys-viewer"),
    ):
        headers = _seed(role, name)
        resp = client.get("/api/v1/chat/conversations", headers=headers)
        assert resp.status_code == 403, name
