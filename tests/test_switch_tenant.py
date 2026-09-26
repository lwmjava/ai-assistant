"""校验成员身份后切换当前租户。"""

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.core.database import engine
from app.core.security import Role, decode_token, hash_password
from app.main import app
from app.models.conversation import Conversation
from app.models.user import Tenant, User
from app.services.membership import ensure_membership

PASSWORD = "Switch@Pass1"


def setup_function() -> None:
    import app.models.conversation  # noqa: F401
    import app.models.membership  # noqa: F401
    import app.models.user  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


def _client() -> TestClient:
    return TestClient(app)


def _tenant(name: str) -> str:
    with Session(engine) as session:
        tenant = Tenant(name=name)
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        return tenant.id


def _user(username: str, role: Role, tenant_id: str) -> str:
    with Session(engine) as session:
        user = User(
            tenant_id=tenant_id,
            username=username,
            hashed_password=hash_password(PASSWORD),
            role=role.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        ensure_membership(session, user)
        return user.id


def _login(client: TestClient, username: str) -> dict:
    resp = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200
    return resp.json()


def test_member_switch_changes_conversation_list_and_invalidates_refresh() -> None:
    client = _client()
    home = _tenant("home")
    other = _tenant("other")
    user_id = _user("ada", Role.MEMBER, home)
    with Session(engine) as session:
        user = session.get(User, user_id)
        assert user is not None
        user.tenant_id = other
        session.add(user)
        session.commit()
        session.refresh(user)
        ensure_membership(session, user)
        user.tenant_id = home
        session.add(user)
        session.commit()
        session.add(Conversation(tenant_id=home, user_id=user_id, title="home-chat"))
        session.add(Conversation(tenant_id=other, user_id=user_id, title="other-chat"))
        session.commit()

    tokens = _login(client, "ada")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    before = client.get("/api/chat/conversations", headers=headers)
    assert before.status_code == 200
    assert {item["title"] for item in before.json()} == {"home-chat"}

    switched = client.post("/api/auth/switch-tenant", headers=headers, json={"tenant_id": other})
    assert switched.status_code == 200
    new_tokens = switched.json()
    assert decode_token(new_tokens["access_token"])["tenant_id"] == other
    assert decode_token(new_tokens["access_token"])["role"] == Role.MEMBER.value

    stale = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert stale.status_code == 401

    listed = client.get(
        "/api/chat/conversations",
        headers={"Authorization": f"Bearer {new_tokens['access_token']}"},
    )
    assert listed.status_code == 200
    assert {item["title"] for item in listed.json()} == {"other-chat"}


def test_non_member_switch_is_forbidden_and_leaves_token_version() -> None:
    client = _client()
    home = _tenant("home")
    other = _tenant("other")
    user_id = _user("ada", Role.MEMBER, home)
    _user("root", Role.SYSTEM_ADMIN, home)
    tokens = _login(client, "ada")
    with Session(engine) as session:
        before = session.get(User, user_id)
        assert before is not None
        version = before.token_version
        tenant_id = before.tenant_id

    denied = client.post(
        "/api/auth/switch-tenant",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json={"tenant_id": other},
    )
    assert denied.status_code == 403

    admin = _login(client, "root")
    admin_denied = client.post(
        "/api/auth/switch-tenant",
        headers={"Authorization": f"Bearer {admin['access_token']}"},
        json={"tenant_id": other},
    )
    assert admin_denied.status_code == 403

    with Session(engine) as session:
        user = session.get(User, user_id)
        assert user is not None
        assert user.tenant_id == tenant_id
        assert user.token_version == version

    missing_body = client.post(
        f"/api/auth/switch-tenant?tenant_id={other}",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert missing_body.status_code == 422
