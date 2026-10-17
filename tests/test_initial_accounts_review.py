"""账号标记、并发改密与隔离环境启动的独立行为验证。"""

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.api.deps import get_db
from app.core.config import settings
from app.core.security import Role, create_access_token, hash_password, verify_password
from app.main import app
from app.models.user import Tenant, User
from app.services.auth_service import InvalidCurrentPasswordError, change_password


@pytest.fixture
def review_engine(monkeypatch):
    isolated = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(isolated)
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUDIT_ENABLED", False)

    def session_override():
        with Session(isolated) as session:
            yield session

    app.dependency_overrides[get_db] = session_override
    yield isolated
    app.dependency_overrides.pop(get_db, None)
    isolated.dispose()


def test_admin_response_preserves_actual_account_flags(review_engine):
    with Session(review_engine) as session:
        tenant = Tenant(name="independent-review")
        session.add(tenant)
        session.flush()
        admin = User(tenant_id=tenant.id, username="review-admin", role=Role.SYSTEM_ADMIN.value,
                     hashed_password=hash_password(secrets.token_urlsafe(24)))
        target = User(tenant_id=tenant.id, username="renamed-test-user", is_test_user=True,
                      must_change_password=True, hashed_password=hash_password(secrets.token_urlsafe(24)))
        session.add_all([admin, target])
        session.commit()
        headers = {"Authorization": "Bearer " + create_access_token(admin.id, admin.tenant_id, admin.role)}
        target_id = target.id
    response = TestClient(app).get("/api/v1/admin/users", headers=headers)
    assert response.status_code == 200
    item = next(item for item in response.json()["items"] if item["id"] == target_id)
    assert item["is_test_user"] is True
    assert item["must_change_password"] is True


def test_stale_password_change_cannot_overwrite_completed_change(review_engine):
    original = secrets.token_urlsafe(24)
    replacement = secrets.token_urlsafe(24)
    with Session(review_engine) as session:
        tenant = Tenant(name="password-race")
        session.add(tenant)
        session.flush()
        user = User(tenant_id=tenant.id, username="concurrent-account", must_change_password=True,
                    hashed_password=hash_password(original))
        session.add(user)
        session.commit()
        user_id = user.id
    with Session(review_engine) as first, Session(review_engine) as second:
        first_user = first.exec(select(User).where(col(User.id) == user_id)).one()
        stale_user = second.exec(select(User).where(col(User.id) == user_id)).one()
        change_password(first, first_user, current_password=original, new_password=replacement)
        with pytest.raises(InvalidCurrentPasswordError):
            change_password(second, stale_user, current_password=original,
                            new_password=secrets.token_urlsafe(24))
    with Session(review_engine) as session:
        user = session.get(User, user_id)
        assert user is not None
        assert user.token_version == 1
        assert verify_password(replacement, user.hashed_password)


def test_environment_accounts_survive_real_application_lifespan(tmp_path):
    """真实环境变量经配置加载和启动引导写入隔离数据库。"""
    repository = Path(__file__).resolve().parents[1]
    child_env = {
        key: value for key, value in os.environ.items()
        if not any(fragment in key.upper() for fragment in ("PASSWORD", "SECRET", "TOKEN", "API_KEY"))
    }
    child_env.update({
        "PYTHONPATH": str(repository),
        "ENV": "development",
        "DATABASE_URL": "sqlite:///" + (tmp_path / "environment-accounts.db").as_posix(),
        "AUTH_ENABLED": "true",
        "AUDIT_ENABLED": "false",
        "LLM_PROVIDER": "mock",
        "EMBEDDING_PROVIDER": "mock",
        "RAG_VECTOR_STORE": "local",
        "RAG_IMPORT_ENABLED": "false",
        "WORKFLOW_ENABLED": "false",
        "EVOLUTION_ENABLED": "false",
        "EVOLUTION_DISTILL_ENABLED": "false",
        "LOG_LEVEL": "CRITICAL",
        "JWT_SECRET_KEY": secrets.token_urlsafe(48),
        "INITIAL_ADMIN_USERNAME": "admin",
        "INITIAL_ADMIN_EMAIL": "",
        "INITIAL_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "INITIAL_SYSTEM_PASSWORD": secrets.token_urlsafe(24),
        "INITIAL_CULTURE_PASSWORD": secrets.token_urlsafe(24),
    })
    script = '''
import os
import socket
from unittest.mock import patch

original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex

def local_connect(connection, address):
    if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
        raise OSError("external network disabled in isolated lifecycle verification")
    return original_connect(connection, address)

def local_connect_ex(connection, address):
    if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
        raise OSError("external network disabled in isolated lifecycle verification")
    return original_connect_ex(connection, address)

# 禁止读取工作区 dotenv，保留真实环境变量 Settings 源。
with patch("pydantic_settings.sources.DotEnvSettingsSource._read_env_files", return_value={}), \\
     patch.object(socket.socket, "connect", local_connect), \\
     patch.object(socket.socket, "connect_ex", local_connect_ex):
    from fastapi.testclient import TestClient
    from sqlmodel import Session, select
    from app.main import app
    from app.core.database import engine
    from app.core.security import verify_password
    from app.models.membership import Membership
    from app.models.user import User

    expected = {"admin": ("system_admin", False), "system": ("tenant_admin", False),
                "culture": ("member", True)}
    with TestClient(app) as client:
        for name, (role, test_user) in expected.items():
            password = os.environ["INITIAL_" + name.upper() + "_PASSWORD"]
            response = client.post("/api/v1/auth/login", json={"username": name, "password": password})
            assert response.status_code == 200
            headers = {"Authorization": "Bearer " + response.json()["access_token"]}
            me = client.get("/api/v1/auth/me", headers=headers)
            assert me.status_code == 200
            assert me.json()["must_change_password"] is True
            assert me.json()["is_test_user"] is test_user
            assert me.json()["role"] == role
            assert client.get("/api/v1/chat/conversations", headers=headers).status_code == 403
        with Session(engine) as session:
            users = session.exec(select(User)).all()
            assert len(users) == 3
            assert len(session.exec(select(Membership)).all()) == 3
            for user in users:
                assert user.email is None
                assert user.role == expected[user.username][0]
                assert user.is_test_user is expected[user.username][1]
                assert user.must_change_password is True
                assert user.hashed_password.startswith("$2")
                assert verify_password(os.environ["INITIAL_" + user.username.upper() + "_PASSWORD"],
                                       user.hashed_password)
    print("isolated-lifecycle-ok")
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=child_env,
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0
    assert "isolated-lifecycle-ok" in result.stdout
