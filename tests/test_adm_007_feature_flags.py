"""功能开关管理 API 测试。"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.main import app
from app.models.feature_flag import FeatureFlag
from app.models.user import Tenant, User
from app.services.feature_flags import apply_persisted_flags, get_flag_value


@pytest.fixture(autouse=True)
def _reset_security_block_flag():
    """每个用例结束后把安全阻断开关恢复到代码默认值，避免跨用例泄漏。"""
    default = settings.SECURITY_BLOCK_ON_INJECTION
    yield
    settings.SECURITY_BLOCK_ON_INJECTION = default
    with Session(engine) as session:
        row = session.get(FeatureFlag, "security_block_on_injection")
        if row is not None:
            session.delete(row)
            session.commit()


def _user(role: str) -> User:
    with Session(engine) as session:
        tenant = Tenant(name=f"ff-{uuid.uuid4().hex[:8]}")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            username=f"ff-{role}-{uuid.uuid4().hex[:6]}",
            hashed_password="x",
            role=role,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return User(
            id=user.id,
            tenant_id=user.tenant_id,
            username=user.username,
            hashed_password=user.hashed_password,
            role=user.role,
            token_version=user.token_version,
            is_active=user.is_active,
        )


def test_system_admin_can_list_and_toggle_flag() -> None:
    admin = _user(Role.SYSTEM_ADMIN.value)
    previous = settings.SECURITY_BLOCK_ON_INJECTION
    previous_audit = settings.AUDIT_ENABLED
    settings.AUDIT_ENABLED = True
    settings.SECURITY_BLOCK_ON_INJECTION = False
    app.dependency_overrides[get_current_user] = lambda: admin
    client = TestClient(app)
    try:
        listed = client.get("/api/v1/admin/features")
        assert listed.status_code == 200
        keys = {row["key"] for row in listed.json()}
        assert "security_block_on_injection" in keys

        patched = client.patch(
            "/api/v1/admin/features/security_block_on_injection",
            json={"value": True},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["value"] is True
        assert settings.SECURITY_BLOCK_ON_INJECTION is True

        with Session(engine) as session:
            row = session.get(FeatureFlag, "security_block_on_injection")
            assert row is not None and row.value is True
            settings.SECURITY_BLOCK_ON_INJECTION = False
            apply_persisted_flags(session)
            assert settings.SECURITY_BLOCK_ON_INJECTION is True
            assert get_flag_value(session, "security_block_on_injection") is True
    finally:
        settings.SECURITY_BLOCK_ON_INJECTION = previous
        settings.AUDIT_ENABLED = previous_audit
        app.dependency_overrides.pop(get_current_user, None)


def test_non_admin_cannot_manage_flags() -> None:
    member = _user(Role.MEMBER.value)
    app.dependency_overrides[get_current_user] = lambda: member
    client = TestClient(app)
    try:
        assert client.get("/api/v1/admin/features").status_code == 403
        assert (
            client.patch(
                "/api/v1/admin/features/security_block_on_injection",
                json={"value": True},
            ).status_code
            == 403
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_unknown_flag_returns_404() -> None:
    admin = _user(Role.SYSTEM_ADMIN.value)
    app.dependency_overrides[get_current_user] = lambda: admin
    client = TestClient(app)
    try:
        assert client.get("/api/v1/admin/features/not_a_flag").status_code == 404
        assert (
            client.patch(
                "/api/v1/admin/features/not_a_flag",
                json={"value": True},
            ).status_code
            == 404
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_persisted_flag_survives_settings_reset() -> None:
    admin = _user(Role.SYSTEM_ADMIN.value)
    previous = settings.SECURITY_BLOCK_ON_INJECTION
    settings.SECURITY_BLOCK_ON_INJECTION = False
    app.dependency_overrides[get_current_user] = lambda: admin
    client = TestClient(app)
    try:
        patched = client.patch(
            "/api/v1/admin/features/security_block_on_injection",
            json={"value": True},
        )
        assert patched.status_code == 200
        assert patched.json()["overridden"] is True
        assert patched.json()["default"] is False

        settings.SECURITY_BLOCK_ON_INJECTION = False
        with Session(engine) as session:
            apply_persisted_flags(session)
        assert settings.SECURITY_BLOCK_ON_INJECTION is True
    finally:
        settings.SECURITY_BLOCK_ON_INJECTION = previous
        app.dependency_overrides.pop(get_current_user, None)
