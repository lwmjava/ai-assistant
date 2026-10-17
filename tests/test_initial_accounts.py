"""环境引导账号和首次改密的真实 HTTP/持久化行为。"""

import secrets
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.core.config import settings
from app.core.database import engine
from app.core.security import Role, hash_password, verify_password
from app.main import app
from app.models.membership import Membership
from app.models.user import Tenant, User
from app.services import auth_service
from app.services.auth_service import ensure_initial_admin


class RedactedPasswords(dict):
    def __repr__(self):
        return "[redacted]"


@pytest.fixture
def initial_accounts(monkeypatch):
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    passwords = RedactedPasswords({name: secrets.token_urlsafe(24) for name in ("admin", "system", "culture")})
    bootstrap_settings = SimpleNamespace(**settings.model_dump())
    bootstrap_settings.INITIAL_ADMIN_USERNAME = "admin"
    bootstrap_settings.INITIAL_ADMIN_PASSWORD = passwords["admin"]
    bootstrap_settings.INITIAL_ADMIN_EMAIL = ""
    bootstrap_settings.INITIAL_SYSTEM_PASSWORD = passwords["system"]
    bootstrap_settings.INITIAL_CULTURE_PASSWORD = passwords["culture"]
    monkeypatch.setattr(auth_service, "settings", bootstrap_settings)
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    yield passwords


def _seed():
    with Session(engine) as session:
        ensure_initial_admin(session)


def test_initial_accounts_persist_roles_flags_and_bcrypt(initial_accounts):
    _seed()
    _seed()
    with Session(engine) as session:
        users = {user.username: user for user in session.exec(select(User)).all()}
        assert set(users) == {"admin", "system", "culture"}
        assert [users[name].role for name in ("admin", "system", "culture")] == [
            Role.SYSTEM_ADMIN.value, Role.TENANT_ADMIN.value, Role.MEMBER.value,
        ]
        for name, user in users.items():
            assert user.must_change_password is True
            assert user.is_test_user is (name == "culture")
            assert user.email is None
            assert user.hashed_password.startswith("$2")
            assert verify_password(initial_accounts[name], user.hashed_password)
            assert session.exec(select(Membership).where(col(Membership.user_id) == user.id)).one()


def test_partial_account_configuration_writes_nothing(initial_accounts, monkeypatch):
    monkeypatch.setattr(auth_service.settings, "INITIAL_CULTURE_PASSWORD", "")
    with pytest.raises(ValueError, match="INITIAL_CULTURE_PASSWORD"):
        _seed()
    with Session(engine) as session:
        assert not session.exec(select(User)).all()
        assert not session.exec(select(Tenant)).all()


@pytest.mark.parametrize("name", ["admin", "system", "culture"])
def test_first_login_requires_change_and_revokes_old_refresh(initial_accounts, name):
    _seed()
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": name, "password": initial_accounts[name]})
    assert login.status_code == 200
    old_tokens = login.json()
    headers = {"Authorization": "Bearer " + old_tokens["access_token"]}
    assert client.get("/api/v1/auth/me", headers=headers).json()["must_change_password"] is True
    for path in ("/api/v1/chat/conversations", "/api/v1/auth/memberships", "/api/v1/admin/users"):
        assert client.get(path, headers=headers).status_code == 403
    new_password = secrets.token_urlsafe(24)
    changed = client.post("/api/v1/auth/change-password", headers=headers, json={
        "current_password": initial_accounts[name], "new_password": new_password,
    })
    assert changed.status_code == 200
    headers = {"Authorization": "Bearer " + changed.json()["access_token"]}
    assert client.get("/api/v1/auth/me", headers=headers).json()["must_change_password"] is False
    assert client.get("/api/v1/chat/conversations", headers=headers).status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": old_tokens["refresh_token"]}).status_code == 401
    old_login = client.post("/api/v1/auth/login", json={"username": name, "password": initial_accounts[name]})
    assert old_login.status_code == 401
    with Session(engine) as session:
        user = session.exec(select(User).where(col(User.username) == name)).one()
        assert user.token_version == 1
        assert verify_password(new_password, user.hashed_password)


def test_failed_change_keeps_password_flag_and_version(initial_accounts):
    _seed()
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": initial_accounts["admin"]})
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    for current, new in ((secrets.token_urlsafe(24), secrets.token_urlsafe(24)),
                         (initial_accounts["admin"], initial_accounts["admin"])):
        assert client.post("/api/v1/auth/change-password", headers=headers, json={
            "current_password": current, "new_password": new,
        }).status_code == 400
    with Session(engine) as session:
        user = session.exec(select(User).where(col(User.username) == "admin")).one()
        assert user.must_change_password is True
        assert user.token_version == 0
        assert verify_password(initial_accounts["admin"], user.hashed_password)


def test_existing_user_is_not_claimed_or_relabelled(initial_accounts):
    with Session(engine) as session:
        tenant = Tenant(name="existing")
        session.add(tenant)
        session.flush()
        user = User(tenant_id=tenant.id, username="culture", hashed_password=hash_password(secrets.token_urlsafe(24)))
        session.add(user)
        session.commit()
    _seed()
    with Session(engine) as session:
        users = session.exec(select(User)).all()
        assert len(users) == 1
        assert users[0].role == Role.MEMBER.value
        assert users[0].is_test_user is False
        assert users[0].must_change_password is False


@pytest.mark.parametrize("legacy", [True, False])
def test_account_flag_migration_preserves_existing_user(tmp_path, monkeypatch, legacy):
    from app.core import database, migration

    url = "sqlite:///" + (tmp_path / "migration.db").as_posix()
    isolated = create_engine(url)
    monkeypatch.setattr(database, "engine", isolated)
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    SQLModel.metadata.create_all(isolated)
    with Session(isolated) as session:
        tenant = Tenant(name="historical")
        session.add(tenant)
        session.flush()
        session.add(User(id="historical", tenant_id=tenant.id, username="historical",
                         hashed_password=hash_password(secrets.token_urlsafe(24))))
        session.commit()
    if legacy:
        with isolated.begin() as connection:
            connection.execute(text("ALTER TABLE users DROP COLUMN must_change_password"))
            connection.execute(text("ALTER TABLE users DROP COLUMN is_test_user"))
        migration.stamp("e4b7a2c85d01")
    assert migration.auto_migrate() is True
    assert migration.get_current_revision() == migration.get_head_revision()
    assert migration.auto_migrate() is True
    with Session(isolated) as session:
        user = session.get(User, "historical")
        assert user is not None
        assert user.is_test_user is False
        assert user.must_change_password is False
    assert {"is_test_user", "must_change_password"}.issubset(
        {column["name"] for column in inspect(isolated).get_columns("users")}
    )
    isolated.dispose()


def test_current_revision_missing_flags_is_rejected(tmp_path, monkeypatch):
    from app.core import database, migration

    url = "sqlite:///" + (tmp_path / "damaged.db").as_posix()
    isolated = create_engine(url)
    monkeypatch.setattr(database, "engine", isolated)
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    SQLModel.metadata.create_all(isolated)
    migration.stamp("head")
    with isolated.begin() as connection:
        connection.execute(text("ALTER TABLE users DROP COLUMN must_change_password"))
    with pytest.raises(RuntimeError, match="用户安全标记列缺失"):
        migration._stamp_if_schema_already_at_head()
    assert "must_change_password" not in {
        column["name"] for column in inspect(isolated).get_columns("users")
    }
    isolated.dispose()


def test_admin_user_responses_preserve_persisted_flags(initial_accounts):
    _seed()
    operator_password = secrets.token_urlsafe(24)
    with Session(engine) as session:
        tenant = session.exec(select(Tenant)).one()
        session.add(User(
            tenant_id=tenant.id, username="operator", role=Role.SYSTEM_ADMIN.value,
            hashed_password=hash_password(operator_password), must_change_password=False,
        ))
        session.commit()
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": "operator", "password": operator_password})
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    listed = client.get("/api/v1/admin/users", headers=headers)
    assert listed.status_code == 200
    users = {user["username"]: user for user in listed.json()["items"]}
    assert users["culture"]["is_test_user"] is True
    assert users["culture"]["must_change_password"] is True
    assert users["system"]["is_test_user"] is False
    assert users["system"]["must_change_password"] is True
    assert users["operator"]["must_change_password"] is False
    culture_id = users["culture"]["id"]
    responses = [client.patch(f"/api/v1/admin/users/{culture_id}", headers=headers, json={"role": "viewer"}),
                 client.post(f"/api/v1/admin/users/{culture_id}/disable", headers=headers),
                 client.post(f"/api/v1/admin/users/{culture_id}/enable", headers=headers)]
    for response in responses:
        assert response.status_code == 200
        assert response.json()["is_test_user"] is True
        assert response.json()["must_change_password"] is True

