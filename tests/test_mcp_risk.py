"""MCP 工具与内置工具共用权限和风险分级。"""

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, select

from app.agents.tools.base import ToolCall, ToolRegistry
from app.agents.tools.contract import RiskLevel
from app.agents.tools.executor import ToolExecutor
from app.core import log_context
from app.core.database import engine
from app.core.security import Role
from app.mcp.adapter import mcp_tool_to_tool
from app.mcp.client import MCPServerTool
from app.models.approval import ActionApproval
from app.models.user import Tenant, User
from app.services.approvals import (
    approve_approval,
    bind_approval_session,
    clear_approval_context,
    execute_approval,
    list_approvals,
)


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict, timeout_seconds: float | None = None) -> str:
        self.calls.append((name, arguments))
        return "updated"


def _engine():
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def session() -> Iterator[Session]:
    with Session(_engine()) as db:
        for uid in ("mcp-risk-member", "mcp-risk-admin"):
            for item in db.exec(select(ActionApproval).where(ActionApproval.requester_id == uid)):
                db.delete(item)
            existing = db.get(User, uid)
            if existing is not None:
                db.delete(existing)
        tenant = db.get(Tenant, "mcp-risk-tenant")
        if tenant is None:
            db.add(Tenant(id="mcp-risk-tenant", name="T1"))
        db.add(User(
            id="mcp-risk-member", tenant_id="mcp-risk-tenant", username="mcp-risk-member", hashed_password="",
            role=Role.MEMBER.value,
        ))
        db.add(User(
            id="mcp-risk-admin", tenant_id="mcp-risk-tenant", username="mcp-risk-admin", hashed_password="",
            role=Role.TENANT_ADMIN.value,
        ))
        db.commit()
        yield db
        for uid in ("mcp-risk-member", "mcp-risk-admin"):
            for item in db.exec(select(ActionApproval).where(ActionApproval.requester_id == uid)):
                db.delete(item)
            existing = db.get(User, uid)
            if existing is not None:
                db.delete(existing)
        db.commit()
    clear_approval_context()
    log_context.clear()


def _user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    assert user is not None
    return user


def test_mcp_tool_shares_risk_enum_with_builtin_tools() -> None:
    tool = mcp_tool_to_tool(
        _Client(),
        MCPServerTool(server="crm", name="read_order", description="read", input_schema={}),
    )
    assert tool.contract is not None
    assert tool.contract.risk == RiskLevel.L0


@pytest.mark.asyncio
async def test_mcp_tool_without_risk_is_rejected() -> None:
    client = _Client()
    tool = mcp_tool_to_tool(
        client,
        MCPServerTool(server="crm", name="mystery", description="d", input_schema={}, risk=None),
    )
    registry = ToolRegistry([tool])
    result = await ToolExecutor(registry).execute(ToolCall(tool.name, {}))
    assert result.status == "denied"
    assert result.error_code == "missing_risk_contract"
    assert client.calls == []


@pytest.mark.asyncio
async def test_high_risk_mcp_tool_needs_approval_before_running(session: Session) -> None:
    client = _Client()
    tool = mcp_tool_to_tool(
        client,
        MCPServerTool(
            server="crm",
            name="update_order",
            description="write",
            input_schema={"type": "object", "additionalProperties": True},
            risk="L2",
            allowed_roles=[Role.TENANT_ADMIN.value],
        ),
    )
    registry = ToolRegistry([tool])
    member = _user(session, "mcp-risk-member")
    admin = _user(session, "mcp-risk-admin")
    bind_approval_session(session)

    log_context.set_user(member.id, member.tenant_id)
    denied = await ToolExecutor(registry).execute(ToolCall(tool.name, {"order_id": 9}))
    assert denied.status == "denied"
    assert denied.error_code == "role_not_allowed"
    assert client.calls == []

    log_context.set_user(admin.id, admin.tenant_id)
    held = await ToolExecutor(registry).execute(ToolCall(tool.name, {"order_id": 9}))
    assert held.status == "denied"
    assert held.error_code == "approval_required"
    assert client.calls == []

    approval = list_approvals(session, admin)[0]
    approve_approval(session, admin, approval.id)
    assert client.calls == []
    await execute_approval(session, admin, approval.id, registry)
    assert client.calls == [("update_order", {"order_id": 9})]


def test_l3_mcp_tool_is_rejected_at_registration() -> None:
    tool = mcp_tool_to_tool(
        _Client(),
        MCPServerTool(server="crm", name="drop_all", description="drop", input_schema={}, risk="L3"),
    )
    with pytest.raises(ValueError):
        ToolRegistry([tool])
