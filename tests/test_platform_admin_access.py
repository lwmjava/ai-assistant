"""Platform-only pages expose matching API boundaries."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.models.user import Tenant, User


@pytest.fixture
def platform_access():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="platform-tenant", name="Platform tenant"))
        for role in ("system_admin", "system_viewer", "tenant_admin", "member", "viewer"):
            session.add(
                User(
                    id=f"user-{role}",
                    tenant_id="platform-tenant",
                    username=f"user-{role}",
                    hashed_password="",
                    role=role,
                )
            )
        session.commit()

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = database
    client = TestClient(app)
    headers = {
        role: {
            "Authorization": f"Bearer {create_access_token(f'user-{role}', 'platform-tenant', role)}"
        }
        for role in ("system_admin", "system_viewer", "tenant_admin", "member", "viewer")
    }
    yield client, headers
    app.dependency_overrides.pop(get_session, None)
    engine.dispose()


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/evaluations/reports",
        "/api/v1/evaluations/jobs",
        "/api/v1/evaluations/datasets",
        "/api/v1/run-history",
        "/api/v1/admin/audit-logs",
    ),
)
def test_platform_management_apis_require_system_admin(platform_access, path):
    client, headers = platform_access
    assert client.get(path, headers=headers["system_admin"]).status_code == 200
    for role in ("system_viewer", "tenant_admin", "member", "viewer"):
        assert client.get(path, headers=headers[role]).status_code == 403
