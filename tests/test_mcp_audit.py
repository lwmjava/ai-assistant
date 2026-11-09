"""MCP 调用写入审计日志，且不落敏感参数明文。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, col, delete, select

from app.agents.tools.base import ToolCall, ToolRegistry
from app.agents.tools.executor import ToolExecutor
from app.audit import AuditAction, AuditLog, reset_audit_logger
from app.core import log_context
from app.core.config import settings
from app.core.database import engine
from app.mcp.adapter import mcp_tool_to_tool
from app.mcp.client import MCPClient, MCPServerTool


@pytest.fixture(autouse=True)
def _clean_mcp_audit():
    SQLModel.metadata.create_all(engine)
    log_context.clear()
    reset_audit_logger()
    with Session(engine) as session:
        session.exec(delete(AuditLog).where(col(AuditLog.action) == AuditAction.MCP_CALL.value))
        session.commit()
    yield
    log_context.clear()
    reset_audit_logger()
    with Session(engine) as session:
        session.exec(delete(AuditLog).where(col(AuditLog.action) == AuditAction.MCP_CALL.value))
        session.commit()


def _mcp_audit_rows() -> list[AuditLog]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(AuditLog)
                .where(col(AuditLog.action) == AuditAction.MCP_CALL.value)
                .order_by(col(AuditLog.created_at))
            )
        )


@pytest.mark.asyncio
async def test_mcp_call_writes_single_audit_record_with_redacted_summary() -> None:
    class SessionStub:
        async def call_tool(self, name: str, arguments: dict):
            return SimpleNamespace(
                isError=False,
                content=[SimpleNamespace(type="text", text=f"ok:{name}")],
            )

    log_context.set_user("mcp-audit-user", "mcp-audit-tenant")
    client = MCPClient.from_session("crm", SessionStub())
    sensitive_val = "plain-token-value-do-not-store"
    out = await client.call_tool(
        "lookup_customer",
        {
            "customer_id": 42,
            "auth_token": sensitive_val,
            "nested": {"user_credential": sensitive_val, "region": "cn"},
            "note": "x" * 60,
        },
    )
    assert out == "ok:lookup_customer"

    rows = _mcp_audit_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.created_at is not None
    assert row.user_id == "mcp-audit-user"
    assert row.tenant_id == "mcp-audit-tenant"
    assert row.resource_type == "mcp_tool"
    assert row.resource_id == "mcp__crm__lookup_customer"
    assert row.details is not None
    assert sensitive_val not in row.details

    payload = json.loads(row.details)
    assert payload["server"] == "crm"
    assert payload["tool"] == "lookup_customer"
    assert payload["result"] == "success"
    assert isinstance(payload["latency_ms"], int | float)
    assert payload["latency_ms"] >= 0
    summary = payload["parameter_summary"]
    assert summary["customer_id"] == 42
    assert summary["auth_token"] == "[redacted]"
    assert summary["nested"] == {"region": "cn", "user_credential": "[redacted]"}
    assert summary["note"] == "[len:60]"


@pytest.mark.asyncio
async def test_mcp_audit_covers_failure_timeout_and_denied(monkeypatch) -> None:
    class FailingSession:
        async def call_tool(self, name: str, arguments: dict):
            if name == "slow":
                await asyncio.sleep(5)
            return SimpleNamespace(
                isError=True,
                content=[SimpleNamespace(type="text", text="bad request")],
            )

    log_context.set_user("mcp-audit-user-2", "mcp-audit-tenant-2")
    client = MCPClient.from_session("erp", FailingSession())
    err_text = await client.call_tool("sync_invoice", {"invoice_id": 7})
    assert err_text.startswith("[MCP 工具错误]")

    monkeypatch.setattr(settings, "MCP_CALL_TIMEOUT_SECONDS", 0.05)
    slow_client = MCPClient.from_session("erp", FailingSession())
    with pytest.raises(TimeoutError):
        await slow_client.call_tool("slow", {"invoice_id": 8})

    unlisted = mcp_tool_to_tool(
        client,
        MCPServerTool(server="erp", name="unlisted_op", description="d", input_schema={}, risk=None),
    )
    reg = ToolRegistry([unlisted])
    denied = await ToolExecutor(reg).execute(ToolCall(unlisted.name, {"invoice_id": 9}))
    assert denied.status == "denied"

    rows = _mcp_audit_rows()
    results = [(r.resource_id, json.loads(r.details or "{}")["result"]) for r in rows]
    assert results == [
        ("mcp__erp__sync_invoice", "failure"),
        ("mcp__erp__slow", "timeout"),
        ("mcp__erp__unlisted_op", "denied"),
    ]
