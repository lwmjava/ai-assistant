"""邀请码加入租户。"""

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.audit.models import AuditAction, AuditLog
from app.core.database import engine
from app.core.security import Role, hash_password
from app.main import app
from app.models.membership import Invitation, Membership
from app.models.user import Tenant, User
from app.services.membership import ensure_membership

PASSWORD = "Invite@Pass1"


def setup_function() -> None:
    import app.models.membership  # noqa: F401
    import app.models.user  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


def _client() -> TestClient:
    return TestClient(app)


def _person(username: str, role: Role, tenant_name: str) -> tuple[str, str]:
    with Session(engine) as session:
        tenant = session.exec(select(Tenant).where(Tenant.name == tenant_name)).first()
        if tenant is None:
            tenant = Tenant(name=tenant_name)
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
        return user.id, tenant.id


def _login(client: TestClient, username: str) -> dict:
    resp = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def _details(log: AuditLog) -> dict:
    raw = log.details
    return json.loads(raw) if isinstance(raw, str) else raw


def test_accept_joins_tenant_without_switching_and_audits_without_code() -> None:
    client = _client()
    _admin_id, home_id = _person("root", Role.SYSTEM_ADMIN, "default")
    member_id, _member_tenant = _person("ada", Role.MEMBER, "ada-home")
    admin_headers = _login(client, "root")
    created = client.post("/api/invitations", headers=admin_headers, json={"tenant_id": home_id})
    assert created.status_code == 201
    code = created.json()["code"]
    assert created.json()["role"] == "member"
    assert created.json()["max_uses"] == 1

    ada = _login(client, "ada")
    joined = client.post("/api/invitations/accept", headers=ada, json={"code": code})
    assert joined.status_code == 201
    assert joined.json()["tenant_id"] == home_id
    assert code not in joined.text

    with Session(engine) as session:
        user = session.get(User, member_id)
        assert user is not None
        assert user.tenant_id != home_id
        assert user.role == Role.MEMBER.value
        membership = session.exec(
            select(Membership).where(
                Membership.user_id == member_id,
                Membership.tenant_id == home_id,
            )
        ).one()
        assert membership.role == "member"
        logs = session.exec(select(AuditLog).where(AuditLog.action == AuditAction.OTHER.value)).all()
        actions = {_details(log)["action"] for log in logs}
        assert actions == {"invite_create", "invite_accept"}
        assert all(code not in str(log.details) for log in logs)

    again = client.post("/api/invitations/accept", headers=ada, json={"code": code})
    assert again.status_code == 409
    assert again.json()["detail"] == "已在租户中"
    with Session(engine) as session:
        invitation = session.exec(select(Invitation).where(Invitation.code == code)).one()
        assert invitation.use_count == 1


def test_expired_and_exhausted_codes_are_rejected() -> None:
    client = _client()
    _admin_id, home_id = _person("root", Role.SYSTEM_ADMIN, "default")
    _person("ada", Role.MEMBER, "ada-home")
    admin_headers = _login(client, "root")
    ada = _login(client, "ada")

    expired = client.post("/api/invitations", headers=admin_headers, json={"tenant_id": home_id})
    expired_code = expired.json()["code"]
    with Session(engine) as session:
        row = session.exec(select(Invitation).where(Invitation.code == expired_code)).one()
        row.expires_at = datetime.now(UTC) - timedelta(days=1)
        session.add(row)
        session.commit()
    resp = client.post("/api/invitations/accept", headers=ada, json={"code": expired_code})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "邀请码已过期"

    used = client.post("/api/invitations", headers=admin_headers, json={"tenant_id": home_id})
    used_code = used.json()["code"]
    with Session(engine) as session:
        row = session.exec(select(Invitation).where(Invitation.code == used_code)).one()
        row.use_count = row.max_uses
        session.add(row)
        session.commit()
    resp = client.post("/api/invitations/accept", headers=ada, json={"code": used_code})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "邀请码已用尽"


def test_member_cannot_create_and_tenant_admin_can() -> None:
    client = _client()
    _person("ada", Role.MEMBER, "ada-home")
    _boss_id, home_id = _person("boss", Role.TENANT_ADMIN, "ada-home")
    ada = _login(client, "ada")
    denied = client.post("/api/invitations", headers=ada, json={})
    assert denied.status_code == 403

    boss = _login(client, "boss")
    created = client.post("/api/invitations", headers=boss, json={})
    assert created.status_code == 201
    assert created.json()["tenant_id"] == home_id

    listed = client.get("/api/invitations", headers=boss)
    assert listed.status_code == 200
    assert listed.json()[0]["code"] == created.json()["code"]


def test_unknown_code_is_404() -> None:
    client = _client()
    _person("ada", Role.MEMBER, "ada-home")
    ada = _login(client, "ada")
    resp = client.post("/api/invitations/accept", headers=ada, json={"code": "missing-code"})
    assert resp.status_code == 404
