"""ShareGPT 训练格式导出、脱敏与审计先于文件返回。"""

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

PHONE = "13800138000"
EMAIL = "alice@example.com"
ID_CARD = "110101199001011234"


@pytest.fixture()
def client():
    app.dependency_overrides.clear()
    opened = TestClient(app)
    opened.__enter__()
    try:
        yield opened
    finally:
        opened.__exit__(None, None, None)
        app.dependency_overrides.clear()


def _tenant_admin_with_chat() -> tuple[User, str]:
    with Session(engine) as session:
        tenant = Tenant(name=f"sharegpt-{uuid.uuid4().hex[:8]}")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            username=f"sharegpt-{uuid.uuid4().hex[:8]}",
            hashed_password="x",
            role=Role.TENANT_ADMIN.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(Membership(user_id=user.id, tenant_id=tenant.id, role=Role.TENANT_ADMIN.value))
        now = datetime.now(UTC)
        conv = Conversation(tenant_id=tenant.id, user_id=user.id, title="训练样例", created_at=now, updated_at=now)
        session.add(conv)
        session.flush()
        user_at = now
        assistant_at = now + timedelta(seconds=1)
        session.add(
            Message(
                conversation_id=conv.id,
                role="user",
                content=f"我的手机是 {PHONE}，邮箱是 {EMAIL}，身份证 {ID_CARD}",
                created_at=user_at,
                updated_at=user_at,
            )
        )
        session.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content="好的，已记录。",
                created_at=assistant_at,
                updated_at=assistant_at,
            )
        )
        session.commit()
        return User(
            id=user.id,
            tenant_id=user.tenant_id,
            username=user.username,
            hashed_password=user.hashed_password,
            role=user.role,
            token_version=user.token_version,
            is_active=user.is_active,
        ), tenant.id


def _member() -> User:
    with Session(engine) as session:
        tenant = Tenant(name=f"sharegpt-member-{uuid.uuid4().hex[:6]}")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            username=f"sharegpt-m-{uuid.uuid4().hex[:6]}",
            hashed_password="x",
            role=Role.MEMBER.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(Membership(user_id=user.id, tenant_id=tenant.id, role=Role.MEMBER.value))
        session.commit()
        return User(
            id=user.id,
            tenant_id=user.tenant_id,
            username=user.username,
            hashed_password=user.hashed_password,
            role=user.role,
            token_version=user.token_version,
            is_active=user.is_active,
        )


def _count_audits(tenant_id: str) -> int:
    with Session(engine) as session:
        return len(
            session.exec(
                select(AuditLog).where(
                    col(AuditLog.action) == "training_export_requested",
                    col(AuditLog.tenant_id) == tenant_id,
                )
            ).all()
        )


def test_sharegpt_export_desensitizes_and_audits_before_bytes(client, monkeypatch) -> None:
    user, tenant_id = _tenant_admin_with_chat()
    monkeypatch.setattr(settings, "AUDIT_ENABLED", True)
    app.dependency_overrides[get_current_user] = lambda: user

    before = _count_audits(tenant_id)

    response = client.post("/api/v1/export/conversations/sharegpt")

    assert response.status_code == 200, response.text
    assert "conversations_sharegpt.json" in response.headers.get("content-disposition", "")
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["id"]
    turns = payload[0]["conversations"]
    assert len(turns) == 2
    assert turns[0]["from"] == "human"
    assert turns[1]["from"] == "gpt"
    assert PHONE not in json.dumps(payload, ensure_ascii=False)
    assert EMAIL not in json.dumps(payload, ensure_ascii=False)
    assert ID_CARD not in json.dumps(payload, ensure_ascii=False)
    assert "***" in turns[0]["value"]

    assert _count_audits(tenant_id) == before + 1
    with Session(engine) as session:
        log = session.exec(
            select(AuditLog).where(
                col(AuditLog.action) == "training_export_requested",
                col(AuditLog.tenant_id) == tenant_id,
            )
        ).first()
        assert log is not None
        details = log.details
        if isinstance(details, str):
            details = json.loads(details)
        assert details["format"] == "sharegpt"
        assert details["message_count"] == 2


def test_sharegpt_export_requires_tenant_admin(client) -> None:
    member = _member()
    app.dependency_overrides[get_current_user] = lambda: member

    response = client.post("/api/v1/export/conversations/sharegpt")

    assert response.status_code == 403


def test_sharegpt_export_audit_disabled_returns_no_text(client, monkeypatch) -> None:
    user, tenant_id = _tenant_admin_with_chat()
    monkeypatch.setattr(settings, "AUDIT_ENABLED", False)
    app.dependency_overrides[get_current_user] = lambda: user

    response = client.post("/api/v1/export/conversations/sharegpt")

    assert response.status_code == 503
    assert response.json()["code"] == "export_audit_failed"
    assert PHONE not in response.text
    assert _count_audits(tenant_id) == 0


def test_sharegpt_export_system_messages_are_skipped(client, monkeypatch) -> None:
    with Session(engine) as session:
        tenant = Tenant(name=f"sharegpt-sys-{uuid.uuid4().hex[:6]}")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            username=f"sharegpt-sys-{uuid.uuid4().hex[:6]}",
            hashed_password="x",
            role=Role.TENANT_ADMIN.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(Membership(user_id=user.id, tenant_id=tenant.id, role=Role.TENANT_ADMIN.value))
        now = datetime.now(UTC)
        conv = Conversation(tenant_id=tenant.id, user_id=user.id, title="系统消息", created_at=now, updated_at=now)
        session.add(conv)
        session.flush()
        session.add(
            Message(
                conversation_id=conv.id,
                role="system",
                content="你是助手",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            Message(
                conversation_id=conv.id,
                role="user",
                content="你好",
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            )
        )
        session.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content="您好",
                created_at=now + timedelta(seconds=2),
                updated_at=now + timedelta(seconds=2),
            )
        )
        session.commit()
        admin = User(
            id=user.id,
            tenant_id=user.tenant_id,
            username=user.username,
            hashed_password=user.hashed_password,
            role=user.role,
            token_version=user.token_version,
            is_active=user.is_active,
        )

    monkeypatch.setattr(settings, "AUDIT_ENABLED", True)
    app.dependency_overrides[get_current_user] = lambda: admin

    response = client.post("/api/v1/export/conversations/sharegpt")

    assert response.status_code == 200
    turns = response.json()[0]["conversations"]
    assert [turn["from"] for turn in turns] == ["human", "gpt"]
    assert all(turn["value"] != "你是助手" for turn in turns)


def test_sharegpt_export_oversized_is_rejected_without_partial_file(client, monkeypatch) -> None:
    user, tenant_id = _tenant_admin_with_chat()
    monkeypatch.setattr(settings, "AUDIT_ENABLED", True)
    import app.services.export_service as export_service

    monkeypatch.setattr(export_service, "MAX_EXPORT_MESSAGES", 1)
    app.dependency_overrides[get_current_user] = lambda: user

    response = client.post("/api/v1/export/conversations/sharegpt")

    assert response.status_code == 413
    assert response.json() == {"code": "export_too_large"}
    assert PHONE not in response.text
    assert _count_audits(tenant_id) == 0
