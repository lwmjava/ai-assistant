"""PAGT-002 工具十要素契约：注册表输出、门控、L3 拦截、超时、审计绑定。"""

import asyncio
import time

import pytest
from sqlmodel import Session, select

from app.agents.tools.base import Tool, ToolCall, ToolRegistry
from app.agents.tools.builtin import default_tools
from app.agents.tools.contract import RiskLevel, ToolContract, ToolPermission
from app.audit import AuditLog, reset_audit_logger
from app.core import log_context
from app.core.database import engine


@pytest.fixture(autouse=True)
def _isolated_context():
    log_context.clear()
    reset_audit_logger()
    yield
    log_context.clear()
    reset_audit_logger()


def _audit_rows(action: str = "tool_call") -> list[AuditLog]:
    with Session(engine) as session:
        return list(session.exec(select(AuditLog).where(AuditLog.action == action)))


def _cleanup(rows: list[AuditLog]) -> None:
    with Session(engine) as session:
        for row in rows:
            got = session.get(AuditLog, row.id)
            if got:
                session.delete(got)
        session.commit()


def test_contract_enum_has_four_levels() -> None:
    assert {r.value for r in RiskLevel} == {"L0", "L1", "L2", "L3"}


def test_describe_contracts_covers_every_default_tool() -> None:
    registry = ToolRegistry(default_tools())
    contracts = registry.describe_contracts()
    names = {item["name"] for item in contracts}
    assert {"calculator", "get_current_datetime", "web_fetch"} <= names
    required = {
        "permission", "risk", "side_effect", "timeout_seconds", "max_retries",
        "idempotent", "conflict_policy", "error_model", "audit", "version",
    }
    for item in contracts:
        missing = required - set(item)
        assert not missing, f"{item['name']} missing fields: {missing}"
        # default_tools() 注册表内每工具必须显式声明契约，不允许 "missing"
        for field in required:
            assert item[field] != "missing", f"{item['name']}.{field} 未声明"


def test_l3_tool_refused_at_registration() -> None:
    contract = ToolContract(
        version="1.0.0",
        permission=ToolPermission(authenticated=True),
        risk=RiskLevel.L3,
        side_effect="irreversible",
        timeout_seconds=10.0,
        max_retries=0,
        idempotent=True,
        conflict_policy="none",
        error_model="observation_string_no_raise",
        audit=True,
    )
    registry = ToolRegistry()
    with pytest.raises(ValueError):
        registry.register(Tool(name="dangerous", description="d", parameters={}, func=lambda a: "x", contract=contract))
    assert registry.get("dangerous") is None


def test_unlisted_contract_none_tool_runs_and_is_audited() -> None:
    """contract=None 未列举工具：放行、必审计 risk=unlisted、describe 标 missing。"""
    async def fake(_: dict) -> str:
        return "should-run"

    registry = ToolRegistry([Tool(name="unlisted", description="u", parameters={}, func=fake)])
    out = asyncio.run(registry.run(ToolCall(name="unlisted", arguments={})))
    assert "should-run" in out
    rows = [r for r in _audit_rows() if r.resource_id == "unlisted"]
    assert rows and '"allowed": true' in (rows[-1].details or "")
    assert '"risk": "unlisted"' in (rows[-1].details or "")
    log_context.clear()
    _cleanup(_audit_rows())

    # describe 对 contract=None 显式标 missing，不静默跳过
    desc = {item["name"]: item for item in registry.describe_contracts()}
    assert desc["unlisted"]["risk"] == "missing"


def test_forged_identity_argument_is_rejected() -> None:
    registry = ToolRegistry(default_tools())
    out = asyncio.run(registry.run(ToolCall(name="calculator", arguments={"expression": "1+1", "tenant_id": "t1"})))
    assert "拒绝" in out
    rows = [r for r in _audit_rows() if r.resource_id == "calculator"]
    assert rows and "forged_identity" in (rows[0].details or "")
    _cleanup(_audit_rows())


def test_tool_call_writes_one_audit_record_without_arguments() -> None:
    log_context.set_user("audit-user-1", "audit-tenant-1")
    registry = ToolRegistry(default_tools())
    out = asyncio.run(registry.run(ToolCall(name="calculator", arguments={"expression": "6*7"})))
    assert "42" in out
    rows = [r for r in _audit_rows() if r.resource_id == "calculator"]
    assert len(rows) == 1
    assert rows[0].user_id == "audit-user-1"
    assert rows[0].tenant_id == "audit-tenant-1"
    assert rows[0].resource_type == "tool"
    # 审计 details 禁止落 arguments
    assert "expression" not in (rows[0].details or "")
    _cleanup(_audit_rows())


def test_timeout_wrapper_triggers_observation() -> None:
    async def slow(_: dict) -> str:
        await asyncio.sleep(0.5)
        return "done"

    contract = ToolContract(
        version="1.0.0",
        permission=ToolPermission(authenticated=False),
        risk=RiskLevel.L0,
        side_effect="none",
        timeout_seconds=0.05,
        max_retries=0,
        idempotent=True,
        conflict_policy="none",
        error_model="observation_string_no_raise",
        audit=True,
    )
    registry = ToolRegistry([Tool(name="slow", description="s", parameters={}, func=slow, contract=contract)])
    started = time.monotonic()
    out = asyncio.run(registry.run(ToolCall(name="slow", arguments={})))
    elapsed = time.monotonic() - started
    assert "超时" in out
    assert elapsed < 0.4, f"timeout 包裹未生效，实际耗时 {elapsed:.2f}s"
    _cleanup(_audit_rows())


def test_exception_still_audited() -> None:
    async def boom(_: dict) -> str:
        raise RuntimeError("internal-boom")

    contract = ToolContract(
        version="1.0.0",
        permission=ToolPermission(authenticated=False),
        risk=RiskLevel.L0,
        side_effect="none",
        timeout_seconds=5.0,
        max_retries=0,
        idempotent=True,
        conflict_policy="none",
        error_model="observation_string_no_raise",
        audit=True,
    )
    registry = ToolRegistry([Tool(name="boom", description="b", parameters={}, func=boom, contract=contract)])
    out = asyncio.run(registry.run(ToolCall(name="boom", arguments={})))
    assert "错误" in out
    rows = [r for r in _audit_rows() if r.resource_id == "boom"]
    assert len(rows) == 1
    assert '"allowed": true' in (rows[0].details or "")
    _cleanup(_audit_rows())
