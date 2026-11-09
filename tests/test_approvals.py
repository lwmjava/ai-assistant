"""审批闭环：发起人决定，过期和撤权不执行，同一次批准只执行一次。"""

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.tools.base import Tool, ToolCall, ToolRegistry
from app.agents.tools.contract import RiskLevel, ToolContract, ToolPermission
from app.agents.tools.executor import ToolExecutor
from app.api.deps import get_current_user, get_db
from app.audit.models import AuditLog
from app.core import log_context
from app.core.security import Role
from app.main import app
from app.models.approval import ActionApproval
from app.models.conversation import Conversation
from app.models.user import Tenant, User
from app.services.agent_state_service import AgentStateService
from app.services.approvals import (
    ApprovalError,
    approve_approval,
    bind_approval_session,
    cancel_approval,
    clear_approval_context,
    execute_approval,
    get_approval,
    list_approvals,
    note_held_approval,
    open_pending,
    reject_approval,
    reject_chat_phrase,
)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 9, 1, 0, 0)

    def __call__(self) -> datetime:
        return self.now


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def session() -> Iterator[Session]:
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(Tenant(id="tenant-b", name="B"))
        db.add(User(
            id="owner", tenant_id="tenant-a", username="owner", hashed_password="",
            role=Role.MEMBER.value, token_version=3,
        ))
        db.add(User(
            id="other", tenant_id="tenant-a", username="other", hashed_password="",
            role=Role.SYSTEM_ADMIN.value,
        ))
        db.add(User(
            id="outsider", tenant_id="tenant-b", username="outsider", hashed_password="",
            role=Role.MEMBER.value,
        ))
        db.add(Conversation(id="conv-1", tenant_id="tenant-a", user_id="owner"))
        db.commit()
        yield db
    clear_approval_context()
    log_context.clear()


def _user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    assert user is not None
    return user


def _tool(calls: list[dict], name: str = "note", risk: RiskLevel = RiskLevel.L2) -> Tool:
    async def _run(arguments: dict) -> str:
        calls.append(arguments)
        return "done"

    return Tool(
        name=name,
        description="记录一次调用",
        parameters={"type": "object", "additionalProperties": True},
        func=_run,
        contract=ToolContract(
            version="1.0.0",
            permission=ToolPermission(authenticated=True, tenant_scoped=True),
            risk=risk,
            side_effect="write",
            timeout_seconds=5.0,
        ),
    )


def _registry(tool: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    registry._tools[tool.name] = tool
    return registry


@pytest.mark.asyncio
async def test_approval_executes_once_and_hides_other_people(session: Session) -> None:
    calls: list[dict] = []
    tool = _tool(calls)
    registry = _registry(tool)
    owner = _user(session, "owner")
    row = open_pending(
        session, owner, resource="tools", action="note", risk="L2", arguments={"path": "a.txt"},
    )
    approve_approval(session, owner, row.id)
    assert calls == []

    first = await execute_approval(session, owner, row.id, registry)
    second = await execute_approval(session, owner, row.id, registry)
    assert calls == [{"path": "a.txt"}]
    assert first.execution_count == 1
    assert second.result_code == first.result_code
    assert "a.txt" in second.parameter_summary
    audits = session.exec(select(AuditLog).where(AuditLog.resource_id == row.id)).all()
    assert audits
    assert all("a.txt" not in (item.details or "") for item in audits)

    with pytest.raises(ApprovalError) as missing:
        get_approval(session, _user(session, "other"), row.id)
    assert missing.value.status_code == 404
    assert row.id not in {item.id for item in list_approvals(session, _user(session, "outsider"))}


def test_chat_phrase_and_api_do_not_approve(session: Session) -> None:
    with pytest.raises(ApprovalError, match="对话回复不能代替审批"):
        reject_chat_phrase("好的")
    source = Path("app/services/chat_service.py").read_text(encoding="utf-8")
    assert "approve_approval" not in source
    owner = _user(session, "owner")
    row = open_pending(
        session, owner, resource="tools", action="note", risk="L2", arguments={"path": "a.txt"},
    )

    def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        client = TestClient(app)
        refused = client.post(f"/api/v1/approvals/{row.id}/approve", json={"text": "好的"})
        assert refused.status_code == 422
        assert session.get(ActionApproval, row.id).status == "pending"
        accepted = client.post(f"/api/v1/approvals/{row.id}/approve", json={})
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "approved"
        assert "arguments_json" not in accepted.json()
        app.dependency_overrides[get_current_user] = lambda: _user(session, "other")
        hidden = client.get(f"/api/v1/approvals/{row.id}")
        assert hidden.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_expiry_revocation_cancel_and_tamper_do_not_run(session: Session) -> None:
    calls: list[dict] = []
    registry = _registry(_tool(calls))
    owner = _user(session, "owner")
    clock = _Clock()
    expired = open_pending(
        session, owner, resource="tools", action="note", risk="L2",
        arguments={"path": "old.txt"}, clock=clock,
    )
    clock.now += timedelta(minutes=31)
    with pytest.raises(ApprovalError):
        approve_approval(session, owner, expired.id, clock)
    assert session.get(ActionApproval, expired.id).status == "expired"

    fresh = open_pending(
        session, owner, resource="tools", action="note", risk="L2",
        arguments={"path": "new.txt"}, clock=clock,
    )
    approve_approval(session, owner, fresh.id, clock)
    owner.token_version += 1
    session.add(owner)
    session.commit()
    with pytest.raises(ApprovalError):
        await execute_approval(session, owner, fresh.id, registry, clock=clock)
    assert session.get(ActionApproval, fresh.id).status == "revoked"

    owner.token_version -= 1
    session.add(owner)
    session.commit()
    again = open_pending(
        session, owner, resource="tools", action="note", risk="L2",
        arguments={"path": "cancel.txt"}, clock=clock,
    )
    approve_approval(session, owner, again.id, clock)
    cancel_approval(session, owner, again.id, clock)
    with pytest.raises(ApprovalError):
        await execute_approval(session, owner, again.id, registry, clock=clock)

    tampered = open_pending(
        session, owner, resource="tools", action="note", risk="L3",
        arguments={"path": "keep.txt"}, clock=clock,
    )
    approve_approval(session, owner, tampered.id, clock)
    stored = session.get(ActionApproval, tampered.id)
    stored.arguments_json = "{}"
    session.add(stored)
    session.commit()
    with pytest.raises(ApprovalError, match="参数已变化"):
        await execute_approval(session, owner, tampered.id, registry, clock=clock)
    assert calls == []


@pytest.mark.asyncio
async def test_budget_failure_does_not_consume_the_approval(session: Session) -> None:
    calls: list[dict] = []
    registry = _registry(_tool(calls))
    owner = _user(session, "owner")
    row = open_pending(
        session, owner, resource="tools", action="note", risk="L2", arguments={"path": "a.txt"},
    )
    approve_approval(session, owner, row.id)

    def _budget() -> None:
        raise RuntimeError("budget")

    with pytest.raises(RuntimeError, match="budget"):
        await execute_approval(session, owner, row.id, registry, before_call=_budget)
    assert session.get(ActionApproval, row.id).status == "approved"
    assert calls == []


@pytest.mark.asyncio
async def test_executor_holds_l2_and_l3_until_execute(session: Session) -> None:
    calls: list[dict] = []
    l2 = _tool(calls, "note", RiskLevel.L2)
    l3 = _tool(calls, "refund", RiskLevel.L3)
    registry = _registry(l2)
    registry._tools[l3.name] = l3
    owner = _user(session, "owner")
    bind_approval_session(session)
    log_context.set_user(owner.id, owner.tenant_id)
    try:
        held = await ToolExecutor(registry).execute(ToolCall("note", {"path": "a.txt"}))
        blocked = await ToolExecutor(registry).execute(ToolCall("refund", {"amount": 1}))
    finally:
        clear_approval_context()
        log_context.clear()
    assert held.error_code == "approval_required"
    assert blocked.error_code == "approval_required"
    assert calls == []
    low = await ToolExecutor(registry).execute(ToolCall("missing", {}))
    assert low.error_code == "tool_not_found"

    note = next(item for item in list_approvals(session, owner) if item.action == "note")
    approve_approval(session, owner, note.id)
    await execute_approval(session, owner, note.id, registry)
    replay = await execute_approval(session, owner, note.id, registry)
    assert calls == [{"path": "a.txt"}]
    assert replay.execution_count == 1


def test_restart_keeps_pending_and_expired_hold_does_not_resume(session: Session) -> None:
    owner = _user(session, "owner")
    row = open_pending(
        session, owner, resource="tools", action="note", risk="L2", arguments={"path": "a.txt"},
    )
    reloaded = session.get(ActionApproval, row.id)
    assert reloaded is not None
    assert reloaded.status == "pending"
    assert reloaded.execution_count == 0

    note_held_approval(row.id)
    store = AgentStateService(session, tenant_id="tenant-a", user_id="owner")
    store.save_checkpoint(
        conversation_id="conv-1",
        message_id=None,
        tenant_id="tenant-a",
        user_id="owner",
        stage="action",
        round_no=0,
        tool_budget_exhausted=False,
        state_json='{"executed_tool_fingerprints": []}',
        status="running",
        error=None,
    )
    loaded = store.load_active("conv-1", None)
    assert loaded is not None
    assert loaded["_approval_status"] == "pending"
    reloaded.expires_at = datetime(2020, 1, 1)
    session.add(reloaded)
    session.commit()
    assert store.load_active("conv-1", None) is None
    clear_approval_context()


def test_reject_then_execute_is_refused(session: Session) -> None:
    owner = _user(session, "owner")
    row = open_pending(
        session, owner, resource="tools", action="note", risk="L2", arguments={"path": "a.txt"},
    )
    rejected = reject_approval(session, owner, row.id)
    assert rejected.status == "rejected"
