"""PAGT-006 Tool Executor：参数校验、超时/重试、聚合审计、bound/legacy golden。

失败边界五类反例覆盖数值边界/失败路径/权限与租户；成功路径与 golden 同守。
隔离测试库 data/test_ai_assistant.db（conftest 钉住）。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import Session, delete, select

from app.agents.tools.base import Tool, ToolCall, ToolRegistry
from app.agents.tools.contract import RiskLevel, ToolContract, ToolPermission
from app.agents.tools.executor import ToolExecutor
from app.audit import AuditLog, reset_audit_logger
from app.core import log_context

L0 = ToolPermission()
L0_CONTRACT = ToolContract(
    version="1.0.0", permission=L0, risk=RiskLevel.L0, side_effect="none",
    timeout_seconds=30.0,
)


def _tool(name, func, contract=None, parameters=None):
    return Tool(
        name=name, description="", parameters=parameters or {"type": "object"},
        func=func, contract=contract,
    )


@pytest.fixture(autouse=True)
def _audit_clean():
    log_context.clear()
    reset_audit_logger()
    with Session(__import__("app.core.database", fromlist=["engine"]).engine) as session:
        session.exec(delete(AuditLog).where(AuditLog.action == "tool_call"))
        session.commit()
    yield
    log_context.clear()
    reset_audit_logger()
    from app.core.database import engine

    with Session(engine) as session:
        session.exec(delete(AuditLog).where(AuditLog.action == "tool_call"))
        session.commit()


def _audit_rows(resource_id: str):
    from app.core.database import engine

    with Session(engine) as session:
        return list(
            session.exec(
                select(AuditLog).where(
                    AuditLog.action == "tool_call", AuditLog.resource_id == resource_id
                )
            )
        )


# ── 数值边界：超时 / max_retries=0 与现状一致 ────────────────────────


def test_executor_timeout_surfaces_status_timeout() -> None:
    async def slow(arguments):
        await asyncio.sleep(0.3)
        return "done"

    tool = _tool("slow", slow, ToolContract(
        version="1", permission=L0, risk=RiskLevel.L0, side_effect="none", timeout_seconds=0.1,
    ))
    reg = ToolRegistry([tool])
    result = asyncio.run(ToolExecutor(reg).execute(ToolCall(name="slow", arguments={})))
    assert result.status == "timeout"
    assert "执行超时" in result.observation
    assert result.attempts == 1


def test_max_retries_zero_matches_legacy_attempts_one() -> None:
    calls = {"n": 0}

    async def flaky(arguments):
        calls["n"] += 1
        raise ConnectionError("boom")

    tool = _tool("flaky", flaky, ToolContract(
        version="1", permission=L0, risk=RiskLevel.L0, side_effect="none",
        timeout_seconds=30.0, max_retries=0, idempotent=True,
    ))
    reg = ToolRegistry([tool])
    result = asyncio.run(ToolExecutor(reg).execute(ToolCall(name="flaky", arguments={})))
    assert calls["n"] == 1  # max_retries=0 与现状逐字节一致，不引入重试
    assert result.attempts == 1


# ── 失败路径：幂等重试 / 非幂等不重试 ────────────────────────────────


def test_idempotent_transient_retries_up_to_contract_max() -> None:
    calls = {"n": 0}
    seq = [ConnectionError("a"), ConnectionError("b")]

    async def flaky(arguments):
        calls["n"] += 1
        if seq:
            raise seq.pop(0)
        return "ok"

    tool = _tool("flaky", flaky, ToolContract(
        version="1", permission=L0, risk=RiskLevel.L0, side_effect="none",
        timeout_seconds=30.0, max_retries=2, idempotent=True,
    ))
    reg = ToolRegistry([tool])
    result = asyncio.run(ToolExecutor(reg).execute(ToolCall(name="flaky", arguments={})))
    assert calls["n"] == 3
    assert result.status == "ok"
    assert result.attempts == 3


def test_non_idempotent_tool_not_auto_retried() -> None:
    calls = {"n": 0}

    async def flaky(arguments):
        calls["n"] += 1
        raise ConnectionError("boom")

    tool = _tool("flaky", flaky, ToolContract(
        version="1", permission=L0, risk=RiskLevel.L1, side_effect="write",
        timeout_seconds=30.0, max_retries=2, idempotent=False,
    ))
    reg = ToolRegistry([tool])
    result = asyncio.run(ToolExecutor(reg).execute(ToolCall(name="flaky", arguments={})))
    assert calls["n"] == 1  # 非幂等不自动重试，副作用不重复
    assert result.attempts == 1


def test_unexpected_exception_becomes_observation_not_pipeline_crash() -> None:
    async def boom(arguments):
        raise RuntimeError("boom")

    tool = _tool("boom", boom, L0_CONTRACT)
    reg = ToolRegistry([tool])
    result = asyncio.run(ToolExecutor(reg).execute(ToolCall(name="boom", arguments={})))
    assert "工具执行错误" in result.observation
    assert result.status == "error"


# ── 参数校验：v1 内置放行 / strict 硬拒绝 ─────────────────────────────


def test_executor_relaxes_validation_for_builtin_tools() -> None:
    from app.agents.tools.builtin import default_tools

    reg = ToolRegistry(default_tools())
    result = asyncio.run(
        ToolExecutor(reg).execute(ToolCall(name="calculator", arguments={}))
    )
    # calculator 自身返回缺参友好文案（golden），Executor 不提前拦截
    assert "未提供 expression 参数" in result.observation
    assert result.audit_metadata["passed"] is False


def test_executor_wrong_type_arg_recorded_in_metadata() -> None:
    from app.agents.tools.builtin import default_tools

    reg = ToolRegistry(default_tools())
    result = asyncio.run(
        ToolExecutor(reg).execute(
            ToolCall(name="calculator", arguments={"expression": 123})
        )
    )
    # v1 放行执行（calculator 内 str() 收敛），类型错误记入 metadata
    assert result.audit_metadata["passed"] is False
    assert any("string" in e for e in result.audit_metadata["errors"])


def test_executor_strict_contract_rejects_bad_args_without_calling_func() -> None:
    calls = {"n": 0}

    async def needme(arguments):
        calls["n"] += 1
        return "ok"

    tool = _tool(
        "needme", needme,
        ToolContract(
            version="1", permission=L0, risk=RiskLevel.L0, side_effect="none",
            timeout_seconds=30.0, strict_validation=True,
        ),
        parameters={"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}}},
    )
    reg = ToolRegistry([tool])
    result = asyncio.run(ToolExecutor(reg).execute(ToolCall(name="needme", arguments={})))
    assert result.status == "invalid"
    assert calls["n"] == 0  # 硬拒绝：func 零调用


# ── 权限与租户：伪造身份拒绝 ──────────────────────────────────────────


def test_forged_identity_argument_rejected() -> None:
    async def echo(arguments):
        return "ok"

    tool = _tool("echo", echo, L0_CONTRACT)
    reg = ToolRegistry([tool])
    result = asyncio.run(
        ToolExecutor(reg).execute(
            ToolCall(name="echo", arguments={"q": "x", "tenant_id": "evil"})
        )
    )
    assert result.status == "denied"
    assert "未授权" in result.observation


# ── 审计聚合：一次逻辑调用一条聚合审计，details 恒四键 ────────────────


def test_logical_call_with_retries_writes_single_aggregated_audit_four_keys() -> None:
    calls = {"n": 0}
    seq = [ConnectionError("a")]

    async def flaky(arguments):
        calls["n"] += 1
        if seq:
            raise seq.pop(0)
        return "ok"

    tool = _tool("flaky", flaky, ToolContract(
        version="1", permission=L0, risk=RiskLevel.L0, side_effect="none",
        timeout_seconds=30.0, max_retries=1, idempotent=True,
    ))
    reg = ToolRegistry([tool])
    asyncio.run(ToolExecutor(reg).execute(ToolCall(name="flaky", arguments={})))
    rows = _audit_rows("flaky")
    assert len(rows) == 1  # 含重试只写一条聚合审计
    import json

    details = json.loads(rows[0].details or "{}")
    assert set(details.keys()) <= {"risk", "allowed", "reason", "latency_ms"}


def test_validation_conclusion_not_in_audit_details() -> None:
    from app.agents.tools.builtin import default_tools

    reg = ToolRegistry(default_tools())
    asyncio.run(ToolExecutor(reg).execute(ToolCall(name="calculator", arguments={})))
    rows = _audit_rows("calculator")
    import json

    for row in rows:
        details = json.loads(row.details or "{}")
        assert "validation" not in details
        assert "passed" not in details
        assert set(details.keys()) <= {"risk", "allowed", "reason", "latency_ms"}


# ── golden：bound 与 legacy 同源同文 ─────────────────────────────────


def test_bound_executor_observation_golden_matches_legacy() -> None:
    from app.agents.tools.builtin import default_tools

    reg = ToolRegistry(default_tools())
    call = ToolCall(name="calculator", arguments={"expression": "1+1"})
    legacy = asyncio.run(reg.run(call))
    bound = asyncio.run(ToolExecutor(reg).execute(call))
    assert bound.observation == legacy


def test_registry_run_unbound_legacy_path_unchanged() -> None:
    from app.agents.tools.builtin import default_tools

    reg = ToolRegistry(default_tools())
    out = asyncio.run(reg.run(ToolCall(name="calculator", arguments={"expression": "2*3"})))
    assert "[calculator]" in out
    assert "结果=6" in out


def test_tool_not_found_observation_and_no_audit() -> None:
    reg = ToolRegistry([])
    result = asyncio.run(ToolExecutor(reg).execute(ToolCall(name="nope", arguments={})))
    assert "未找到" in result.observation
    assert result.status == "error"
    assert _audit_rows("nope") == []
