"""默认管线与工具短路径必须走同一个 ToolExecutor。

入口是生产函数 ``iter_fast_path`` 和 ``AgentPipeline._iter_action_loop``。
``wired`` 复现 ChatService 的显式注入；``unwired`` 复现忘记注入时的回退。
隔离库只用于审计和角色行，不碰业务对话数据。
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlmodel import Session, col, delete, select

from app.agents.fast_path import iter_fast_path
from app.agents.pipeline import AgentPipeline, AgentState, ExecutionPolicy
from app.agents.route import ChatRoute, RouteKind
from app.agents.tools.base import Tool, ToolRegistry
from app.agents.tools.builtin import default_tools
from app.agents.tools.contract import RiskLevel, ToolContract, ToolPermission
from app.agents.tools.executor import ToolExecutor
from app.audit.models import AuditLog
from app.core import log_context
from app.core.database import engine
from app.core.security import Role
from app.llm.base import LLMOptions, LLMProvider
from app.models.user import Tenant, User

_PATHS = ("fast", "wired", "unwired")


class ScriptLLM(LLMProvider):
    model = "tool-path-script"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)

    async def chat(self, messages, options=None) -> str:
        if not self.replies:
            return "完成"
        return self.replies.pop(0)

    async def stream_chat(self, messages, options=None):
        yield await self.chat(messages, options)


def _contract(**overrides) -> ToolContract:
    data = dict(
        version="path-v1",
        permission=ToolPermission(),
        risk=RiskLevel.L0,
        side_effect="none",
        timeout_seconds=1.0,
        max_retries=0,
        idempotent=True,
        strict_validation=False,
    )
    data.update(overrides)
    return ToolContract(**data)


def _registry(func, **contract_overrides) -> ToolRegistry:
    parameters = contract_overrides.pop("parameters", {"type": "object", "properties": {}})
    tool = Tool(
        name="path_probe",
        description="路径契约探测",
        parameters=parameters,
        func=func,
        contract=_contract(**contract_overrides),
    )
    return ToolRegistry([tool])


def _call(expression: str = "{}") -> str:
    return f'<tool_call>{{"name":"path_probe","arguments":{expression}}}</tool_call>'


@pytest.fixture(autouse=True)
def _clean_context():
    log_context.clear()
    created: list[tuple[str, str]] = []
    yield created
    log_context.clear()
    with Session(engine) as session:
        session.exec(delete(AuditLog).where(col(AuditLog.resource_id) == "path_probe"))
        session.exec(delete(AuditLog).where(col(AuditLog.resource_id) == "calculator"))
        for user_id, tenant_id in created:
            user = session.get(User, user_id)
            tenant = session.get(Tenant, tenant_id)
            if user is not None:
                session.delete(user)
            if tenant is not None:
                session.delete(tenant)
        session.commit()


def _remember(box: list[tuple[str, str]], role: str) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:12]
    with Session(engine) as session:
        tenant = Tenant(name=f"v1c002-{suffix}")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            username=f"v1c002-{suffix}",
            hashed_password="not-a-secret",
            role=role,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        box.append((user.id, tenant.id))
        return user.id, tenant.id


def _audits(name: str = "path_probe") -> list[AuditLog]:
    with Session(engine) as session:
        rows = list(session.exec(select(AuditLog).where(col(AuditLog.resource_id) == name)))
    return rows


def _details(row: AuditLog) -> dict:
    raw = row.details
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw or {})


def _drive(path: str, llm: LLMProvider, registry: ToolRegistry, *, rounds: int = 2) -> AgentState:
    async def _run() -> AgentState:
        state = AgentState(user_input="请使用已注册工具")
        names = frozenset(tool.name for tool in registry.all())
        if path == "fast":
            route = ChatRoute(kind=RouteKind.TOOLS, tool_names=names)
            async for _event in iter_fast_path(llm, LLMOptions(), state, route, None, registry):
                pass
            return state
        pipeline = AgentPipeline(llm, LLMOptions(), tools=registry)
        pipeline.max_tool_rounds = rounds
        if path == "wired":
            pipeline.executor = ToolExecutor(registry)
        policy = ExecutionPolicy(allowed_tool_names=names)
        async for _event in pipeline._iter_action_loop(state, policy):
            pass
        return state

    return asyncio.run(_run())


def _status(state: AgentState) -> str:
    last = state.tool_results[-1]
    return getattr(last, "status", "")


@pytest.mark.parametrize("path", _PATHS)
def test_both_paths_enter_executor_and_retry_once(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    original = ToolExecutor.execute

    async def wrapped(self, call):
        seen.append(call.name)
        return await original(self, call)

    monkeypatch.setattr(ToolExecutor, "execute", wrapped)
    calls = {"n": 0}

    async def flaky(_arguments):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("下游暂时不可用")
        return "ok"

    state = _drive(
        path,
        ScriptLLM([_call(), "完成"]),
        _registry(flaky, max_retries=1, idempotent=True),
    )
    assert seen == ["path_probe"]
    assert calls["n"] == 2
    assert _status(state) == "ok"
    rows = _audits()
    assert len(rows) == 1
    assert _details(rows[0])["allowed"] is True
    assert set(_details(rows[0])) == {"risk", "allowed", "reason", "latency_ms"}


@pytest.mark.parametrize("path", _PATHS)
def test_non_idempotent_connection_failure_does_not_retry(path: str) -> None:
    calls = {"n": 0}

    async def flaky(_arguments):
        calls["n"] += 1
        raise ConnectionError("下游暂时不可用")

    state = _drive(
        path,
        ScriptLLM([_call(), "完成"]),
        _registry(flaky, max_retries=3, idempotent=False),
    )
    assert calls["n"] == 1
    assert _status(state) == "error"
    assert len(_audits()) == 1


@pytest.mark.parametrize("path", _PATHS)
def test_timeout_is_terminal_without_extra_attempts(path: str) -> None:
    calls = {"n": 0}

    async def slow(_arguments):
        calls["n"] += 1
        await asyncio.sleep(0.2)
        return "late"

    state = _drive(
        path,
        ScriptLLM([_call(), "完成"]),
        _registry(slow, timeout_seconds=0.05, max_retries=0, idempotent=True),
    )
    assert calls["n"] == 1
    assert _status(state) == "timeout"
    assert len(_audits()) == 1


@pytest.mark.parametrize("path", _PATHS)
def test_idempotent_timeout_retries_then_stays_timeout(path: str) -> None:
    calls = {"n": 0}

    async def slow(_arguments):
        calls["n"] += 1
        await asyncio.sleep(0.2)
        return "late"

    state = _drive(
        path,
        ScriptLLM([_call(), "完成"]),
        _registry(slow, timeout_seconds=0.05, max_retries=1, idempotent=True),
    )
    assert calls["n"] == 2
    assert _status(state) == "timeout"
    assert len(_audits()) == 1


@pytest.mark.parametrize("path", _PATHS)
def test_strict_invalid_arguments_do_not_execute(path: str) -> None:
    calls = {"n": 0}

    async def probe(_arguments):
        calls["n"] += 1
        return "ran"

    state = _drive(
        path,
        ScriptLLM([_call(), "完成"]),
        _registry(
            probe,
            strict_validation=True,
            parameters={
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "integer"}},
            },
        ),
    )
    assert calls["n"] == 0
    assert _status(state) == "invalid"
    details = _details(_audits()[0])
    assert details["allowed"] is False
    assert details["reason"] == "invalid_arguments"


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize(
    ("permission", "arguments", "reason"),
    [
        (ToolPermission(authenticated=True), "{}", "authentication_required"),
        (ToolPermission(tenant_scoped=True), "{}", "tenant_context_required"),
        (ToolPermission(), '{"tenant_id":"other"}', "forged_identity_argument"),
    ],
)
def test_identity_and_tenant_refusals_share_terminal_audit(
    path: str, permission: ToolPermission, arguments: str, reason: str
) -> None:
    calls = {"n": 0}

    async def probe(_arguments):
        calls["n"] += 1
        return "ran"

    state = _drive(
        path,
        ScriptLLM([_call(arguments), "完成"]),
        _registry(probe, permission=permission),
    )
    assert calls["n"] == 0
    assert _status(state) == "denied"
    details = _details(_audits()[0])
    assert details["allowed"] is False
    assert details["reason"] == reason


def test_role_revocation_is_read_on_each_call(_clean_context) -> None:
    user_id, tenant_id = _remember(_clean_context, Role.SYSTEM_ADMIN.value)
    calls = {"n": 0}

    async def probe(_arguments):
        calls["n"] += 1
        return "ran"

    registry = _registry(
        probe,
        permission=ToolPermission(authenticated=True, allowed_roles=frozenset({Role.SYSTEM_ADMIN.value})),
    )
    log_context.set_user(user_id, tenant_id)
    allowed = _drive("fast", ScriptLLM([_call(), "完成"]), registry)
    assert calls["n"] == 1
    assert _status(allowed) == "ok"

    with Session(engine) as session:
        user = session.get(User, user_id)
        assert user is not None
        user.role = Role.MEMBER.value
        session.add(user)
        session.commit()
    calls["n"] = 0
    denied = _drive("wired", ScriptLLM([_call(), "完成"]), registry)
    assert calls["n"] == 0
    assert _status(denied) == "denied"
    reasons = [_details(row)["reason"] for row in _audits()]
    assert None in reasons
    assert "role_not_allowed" in reasons


@pytest.mark.parametrize("path", _PATHS)
def test_duplicate_fingerprint_does_not_execute_again(path: str) -> None:
    calls = {"n": 0}

    async def probe(_arguments):
        calls["n"] += 1
        return "ok"

    state = _drive(path, ScriptLLM([_call('{"n":1}'), _call('{"n":1}'), "完成"]), _registry(probe))
    assert calls["n"] == 1
    assert any("相同参数已执行过" in str(item) for item in state.tool_results)


@pytest.mark.parametrize("path", ["fast", "wired"])
def test_round_cap_stops_further_execution(path: str) -> None:
    calls = {"n": 0}

    async def probe(arguments):
        calls["n"] += 1
        return str(arguments.get("n"))

    replies = [_call('{"n":1}'), _call('{"n":2}'), _call('{"n":3}'), "完成"]
    _drive(path, ScriptLLM(replies), _registry(probe), rounds=2)
    assert calls["n"] == 2


@pytest.mark.parametrize("path", _PATHS)
def test_cancel_propagates_without_success_audit(path: str) -> None:
    async def hang(_arguments):
        await asyncio.Event().wait()

    async def _cancel() -> None:
        task = asyncio.create_task(
            _drive_async(path, ScriptLLM([_call(), "完成"]), _registry(hang))
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_cancel())
    assert _audits() == []


async def _drive_async(path: str, llm: LLMProvider, registry: ToolRegistry) -> AgentState:
    state = AgentState(user_input="请使用已注册工具")
    names = frozenset(tool.name for tool in registry.all())
    if path == "fast":
        route = ChatRoute(kind=RouteKind.TOOLS, tool_names=names)
        async for _event in iter_fast_path(llm, LLMOptions(), state, route, None, registry):
            pass
        return state
    pipeline = AgentPipeline(llm, LLMOptions(), tools=registry)
    pipeline.max_tool_rounds = 2
    if path == "wired":
        pipeline.executor = ToolExecutor(registry)
    async for _event in pipeline._iter_action_loop(state, ExecutionPolicy(allowed_tool_names=names)):
        pass
    return state


@pytest.mark.parametrize("path", _PATHS)
def test_non_finite_calculator_is_error_on_both_paths(path: str) -> None:
    registry = ToolRegistry([tool for tool in default_tools() if tool.name == "calculator"])
    reply = '<tool_call>{"name":"calculator","arguments":{"expression":"1e309"}}</tool_call>'
    state = _drive(path, ScriptLLM([reply, "完成"]), registry)
    assert _status(state) == "error"
    assert "不是有限数值" in str(state.tool_results[-1])
    assert _audits("calculator")


@pytest.mark.parametrize("path", _PATHS)
def test_disallowed_tool_name_is_not_executed(path: str) -> None:
    calls = {"n": 0}

    async def probe(_arguments):
        calls["n"] += 1
        return "ran"

    registry = _registry(probe)

    async def _run() -> AgentState:
        state = AgentState(user_input="请使用已注册工具")
        if path == "fast":
            route = ChatRoute(kind=RouteKind.TOOLS, tool_names=frozenset({"calculator"}))
            async for _event in iter_fast_path(
                ScriptLLM([_call(), "完成"]), LLMOptions(), state, route, None, registry
            ):
                pass
            return state
        pipeline = AgentPipeline(ScriptLLM([_call(), "完成"]), LLMOptions(), tools=registry)
        pipeline.max_tool_rounds = 2
        pipeline.executor = ToolExecutor(registry)
        policy = ExecutionPolicy(allowed_tool_names=frozenset({"calculator"}))
        async for _event in pipeline._iter_action_loop(state, policy):
            pass
        return state

    state = asyncio.run(_run())
    assert calls["n"] == 0
    assert any("不允许" in str(item) for item in state.tool_results)


def test_audit_identity_comes_from_context_not_arguments(_clean_context) -> None:
    user_id, tenant_id = _remember(_clean_context, Role.MEMBER.value)
    log_context.set_user(user_id, tenant_id)

    async def probe(_arguments):
        return "ok"

    _drive("fast", ScriptLLM([_call('{"n":1}'), "完成"]), _registry(probe))
    row = _audits()[0]
    assert row.user_id == user_id
    assert row.tenant_id == tenant_id
