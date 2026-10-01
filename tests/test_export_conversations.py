"""导出租户对话。只使用测试库。"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.api.deps import get_current_user
from app.audit.models import AuditLog
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.main import app
from app.models.conversation import Conversation, Message
from app.models.membership import Membership
from app.models.user import Tenant, User
from app.services.export_service import MAX_EXPORT_BYTES, MAX_EXPORT_MESSAGES
from app.services.membership import switch_tenant

ALPHA = "alpha-export-sentence"
BETA = "beta-export-sentence"
MARKER = "export-marker-9f3a"


def _tenant(name: str) -> Tenant:
    with Session(engine) as session:
        tenant = Tenant(name=name)
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        session.expunge(tenant)
        return tenant


def _user(tenant_id: str, role: str, username: str) -> User:
    with Session(engine) as session:
        user = User(
            tenant_id=tenant_id,
            username=f"{username}-{uuid.uuid4().hex[:8]}",
            hashed_password="not-a-login",
            role=role,
            token_version=0,
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.expunge(user)
        return user


def _membership(user_id: str, tenant_id: str, role: str) -> None:
    with Session(engine) as session:
        session.add(Membership(user_id=user_id, tenant_id=tenant_id, role=role))
        session.commit()


def _conversation(
    tenant_id: str,
    user_id: str,
    title: str,
    messages: list[tuple[str, str]],
    created_at: datetime,
) -> None:
    with Session(engine) as session:
        conversation = Conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            title=title,
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(conversation)
        session.flush()
        for index, (role, content) in enumerate(messages):
            moment = created_at + timedelta(seconds=index + 1)
            session.add(
                Message(
                    conversation_id=conversation.id,
                    role=role,
                    content=content,
                    created_at=moment,
                    updated_at=moment,
                )
            )
        session.commit()


def _audits(tenant_id: str) -> list[AuditLog]:
    with Session(engine) as session:
        rows = session.exec(
            select(AuditLog).where(
                col(AuditLog.action) == "export_requested",
                col(AuditLog.resource_id) == tenant_id,
            )
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)


@pytest.fixture()
def client():
    holder: dict[str, User] = {}

    def _open(user: User) -> TestClient:
        previous = holder.get("client")
        if isinstance(previous, TestClient):
            previous.__exit__(None, None, None)
        holder["user"] = user
        app.dependency_overrides[get_current_user] = lambda: holder["user"]
        opened = TestClient(app)
        opened.__enter__()
        holder["client"] = opened
        return opened

    yield _open
    opened = holder.get("client")
    if isinstance(opened, TestClient):
        opened.__exit__(None, None, None)
    app.dependency_overrides.clear()


def test_export_caps_match_the_single_download_limit() -> None:
    assert MAX_EXPORT_MESSAGES == 5000
    assert MAX_EXPORT_BYTES == 32 * 1024 * 1024


def test_tenant_admin_downloads_only_the_token_tenant(client) -> None:
    first = _tenant("export-alpha")
    second = _tenant("export-beta")
    admin = _user(first.id, Role.MEMBER.value, "export-admin-alpha")
    _membership(admin.id, first.id, Role.TENANT_ADMIN.value)
    other = _user(second.id, Role.TENANT_ADMIN.value, "export-admin-beta")
    _membership(other.id, second.id, Role.TENANT_ADMIN.value)
    earlier = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    later = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    _conversation(first.id, admin.id, "后写的会话", [("user", "second-turn"), ("assistant", ALPHA)], later)
    _conversation(first.id, admin.id, "先写的会话", [("user", "first-turn")], earlier)
    _conversation(second.id, other.id, "乙的会话", [("user", BETA)], earlier)

    http = client(admin)
    seen: dict[str, bool] = {}

    def _after_audit(raw: bytes):
        with Session(engine) as session:
            row = session.exec(
                select(AuditLog).where(
                    col(AuditLog.action) == "export_requested",
                    col(AuditLog.resource_id) == first.id,
                )
            ).first()
        seen["audit_before_body"] = row is not None
        yield raw

    import app.api.routes.export as export_route

    export_route.iter_export_bytes = _after_audit
    try:
        response = http.post("/api/export/conversations")
    finally:
        import app.services.export_service as export_service

        export_route.iter_export_bytes = export_service.iter_export_bytes

    assert response.status_code == 200
    assert seen["audit_before_body"] is True
    assert "attachment" in response.headers.get("content-disposition", "")
    body = response.json()
    assert body["tenant_id"] == first.id
    assert body["exported_at"]
    assert [item["title"] for item in body["conversations"]] == ["先写的会话", "后写的会话"]
    assert [item["content"] for item in body["conversations"][1]["messages"]] == ["second-turn", ALPHA]
    assert BETA not in response.text
    logs = _audits(first.id)
    assert len(logs) == 1
    details = logs[0].details
    if isinstance(details, str):
        details = json.loads(details)
    assert details["conversation_count"] == 2
    assert details["message_count"] == 3
    assert details["actor_id"] == admin.id
    assert ALPHA not in json.dumps(details, ensure_ascii=False)
    assert _audits(second.id) == []


def test_other_roles_and_switched_member_are_refused(client) -> None:
    first = _tenant("export-role-a")
    second = _tenant("export-role-b")
    member = _user(first.id, Role.MEMBER.value, "export-member")
    _membership(member.id, first.id, Role.MEMBER.value)
    viewer = _user(first.id, Role.VIEWER.value, "export-viewer")
    _membership(viewer.id, first.id, Role.VIEWER.value)
    admin_user = _user(first.id, Role.SYSTEM_ADMIN.value, "export-sysadmin")
    switched = _user(first.id, Role.TENANT_ADMIN.value, "export-switched")
    _membership(switched.id, first.id, Role.TENANT_ADMIN.value)
    _membership(switched.id, second.id, Role.MEMBER.value)
    moment = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    _conversation(first.id, member.id, "甲", [("user", ALPHA)], moment)
    _conversation(second.id, switched.id, "乙", [("user", BETA)], moment)

    for user in (member, viewer, admin_user):
        response = client(user).post("/api/export/conversations")
        assert response.status_code == 403
        assert ALPHA not in response.text
        assert BETA not in response.text

    with Session(engine) as session:
        stored = session.get(User, switched.id)
        assert stored is not None
        switch_tenant(session, stored, second.id)
        session.refresh(stored)
        session.expunge(stored)
    assert stored.role == Role.TENANT_ADMIN.value
    assert stored.tenant_id == second.id
    refused = client(stored).post("/api/export/conversations")
    assert refused.status_code == 403
    assert BETA not in refused.text
    assert ALPHA not in refused.text
    assert _audits(first.id) == []
    assert _audits(second.id) == []

    mismatched = client(stored).post(
        "/api/export/conversations",
        json={"tenant_id": first.id},
    )
    assert mismatched.status_code == 403
    assert BETA not in mismatched.text


def test_audit_failure_returns_no_conversation_text(client, monkeypatch) -> None:
    tenant = _tenant("export-audit")
    admin = _user(tenant.id, Role.TENANT_ADMIN.value, "export-audit-admin")
    _membership(admin.id, tenant.id, Role.TENANT_ADMIN.value)
    _conversation(tenant.id, admin.id, "原文", [("user", ALPHA)], datetime(2026, 10, 1, 11, 0, tzinfo=UTC))
    http = client(admin)

    monkeypatch.setattr(settings, "AUDIT_ENABLED", False)
    closed = http.post("/api/export/conversations")
    assert closed.status_code == 503
    assert closed.json()["code"] == "export_audit_failed"
    assert ALPHA not in closed.text
    assert _audits(tenant.id) == []

    monkeypatch.setattr(settings, "AUDIT_ENABLED", True)

    class BrokenAudit:
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("audit down")

    monkeypatch.setattr("app.services.export_service.AuditLog", BrokenAudit)
    failed = http.post("/api/export/conversations")
    assert failed.status_code == 503
    assert ALPHA not in failed.text
    assert _audits(tenant.id) == []


def test_oversized_export_is_rejected_without_a_partial_file(client, monkeypatch, caplog) -> None:
    import app.services.export_service as export_service

    tenant = _tenant("export-size")
    admin = _user(tenant.id, Role.TENANT_ADMIN.value, "export-size-admin")
    _membership(admin.id, tenant.id, Role.TENANT_ADMIN.value)
    moment = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    _conversation(
        tenant.id,
        admin.id,
        "三条",
        [("user", "one"), ("assistant", "two"), ("user", "three")],
        moment,
    )
    monkeypatch.setattr(export_service, "MAX_EXPORT_MESSAGES", 2)
    http = client(admin)
    too_many = http.post("/api/export/conversations")
    assert too_many.status_code == 413
    assert too_many.json() == {"code": "export_too_large"}
    assert "one" not in too_many.text
    assert _audits(tenant.id) == []

    monkeypatch.setattr(export_service, "MAX_EXPORT_MESSAGES", MAX_EXPORT_MESSAGES)
    monkeypatch.setattr(export_service, "MAX_EXPORT_BYTES", 40)
    wide = _tenant("export-wide")
    wide_admin = _user(wide.id, Role.TENANT_ADMIN.value, "export-wide-admin")
    _membership(wide_admin.id, wide.id, Role.TENANT_ADMIN.value)
    _conversation(wide.id, wide_admin.id, "大", [("user", MARKER + ("x" * 80))], moment)
    caplog.set_level("INFO")
    huge = client(wide_admin).post("/api/export/conversations")
    assert huge.status_code == 413
    assert huge.json() == {"code": "export_too_large"}
    assert MARKER not in huge.text
    assert MARKER not in caplog.text
    assert _audits(wide.id) == []

    monkeypatch.setattr(export_service, "MAX_EXPORT_BYTES", 180)
    split = _tenant("export-split")
    split_admin = _user(split.id, Role.TENANT_ADMIN.value, "export-split-admin")
    _membership(split_admin.id, split.id, Role.TENANT_ADMIN.value)
    _conversation(
        split.id,
        split_admin.id,
        "两段",
        [("user", "a" * 80), ("assistant", "b" * 80)],
        moment,
    )
    both = client(split_admin).post("/api/export/conversations")
    assert both.status_code == 413
    assert "aaaa" not in both.text
    assert _audits(split.id) == []


def test_paging_keeps_order(client, monkeypatch) -> None:
    import app.services.export_service as export_service

    monkeypatch.setattr(export_service, "_PAGE_SIZE", 1)
    tenant = _tenant("export-page")
    admin = _user(tenant.id, Role.TENANT_ADMIN.value, "export-page-admin")
    _membership(admin.id, tenant.id, Role.TENANT_ADMIN.value)
    moment = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
    _conversation(tenant.id, admin.id, "仅一条", [("user", "paged-line")], moment)
    response = client(admin).post("/api/export/conversations")
    assert response.status_code == 200
    assert response.json()["conversations"][0]["messages"][0]["content"] == "paged-line"
