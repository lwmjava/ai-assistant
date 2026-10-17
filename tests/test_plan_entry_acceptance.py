"""Independent E2 entry acceptance: real HTTP/service/runtime/SQLite/audit.

The scripted provider proves deterministic control flow, not model quality.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.agents.pipeline import AgentPipeline
from app.agents.prompts import SYSTEM_ACT, SYSTEM_PLAN, SYSTEM_PLAN_LEGACY, SYSTEM_PREFLOW
from app.agents.tools.base import Tool, ToolRegistry
from app.agents.tools.contract import RiskLevel, ToolContract, ToolPermission
from app.agents.tools.outcome import ToolOutput
from app.api.deps import get_current_user, get_db
from app.api.routes.chat import router
from app.audit.models import AuditLog
from app.core import log_context
from app.core.config import settings
from app.llm.base import LLMProvider
from app.llm.factory import set_llm_provider_override
from app.models.agent_state import AgentRunState
from app.models.conversation import Message
from app.models.user import Tenant, User
from app.services.chat_service import ChatService


def _terminal_status(body):
    for frame in body.replace("\r\n", "\n").split("\n\n"):
        if "event: run.finished\n" in frame:
            data = next(line[5:].strip() for line in frame.splitlines() if line.startswith("data:"))
            return json.loads(data)["status"]
    raise AssertionError("缺少运行终态")


def entry_plan(kind="tool"):
    steps = []
    for index in range(1, 4):
        step = dict(id=f"step{index}", description=f"produce {index}", kind=kind,
                    depends_on=[] if index == 1 else [f"step{index - 1}"], expected_output="value")
        if kind == "tool":
            step.update(tool="entry_probe", arguments={"value": index})
        steps.append(step)
    return {"schema_version": 1, "steps": steps}


class EntryProvider(LLMProvider):
    model = "entry-plan-script-v1"

    def __init__(self):
        self.plan = entry_plan()
        self.reason = "reason result"
        self.reason_timeout = False
        self.legacy_calls = 0
        self.plans = 0

    async def chat(self, messages, options=None):
        system = messages[0].content
        if system == SYSTEM_PREFLOW:
            return "YES"
        if system == SYSTEM_PLAN:
            self.plans += 1
            return json.dumps(self.plan)
        if system == SYSTEM_PLAN_LEGACY:
            return "1. use entry_probe\n2. reply"
        if system.startswith("只完成当前推理步骤"):
            if self.reason_timeout:
                await asyncio.sleep(1)
            return self.reason
        if system == SYSTEM_ACT:
            if "1. use entry_probe" in messages[-1].content and self.legacy_calls == 0:
                self.legacy_calls += 1
                return '<tool_call>{"name":"entry_probe","arguments":{"value":9}}</tool_call>'
            return "draft"
        return "entry answer"

    async def stream_chat(self, messages, options=None):
        yield await self.chat(messages, options)


@pytest.fixture
def entry(monkeypatch):
    import app.api.routes.chat as chat_module
    import app.audit.logger as audit_module
    import app.core.database as db_module

    db = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db)
    monkeypatch.setattr(db_module, "engine", db)
    monkeypatch.setattr(audit_module, "engine", db)
    for name, value in {"RAG_ENABLED": False, "AGENT_STATE_PERSIST_ENABLED": True,
                        "AGENT_ORCHESTRATION": "self", "AGENT_QUALITY_GATE_ENABLED": False,
                        "AGENT_MAX_TOOL_ROUNDS": 3, "LLM_TIMEOUT": 0.05}.items():
        monkeypatch.setattr(settings, name, value)
    provider = EntryProvider()
    set_llm_provider_override(provider)
    monkeypatch.setattr(chat_module, "_service", ChatService(llm_provider=provider))
    calls = []
    control = {"failure": False}

    async def probe(arguments):
        calls.append(arguments["value"])
        if control["failure"] and arguments["value"] == 2:
            return ToolOutput("synthetic business failure", status="error", error_code="business_failure")
        return f"value {arguments['value']}"

    contract = ToolContract(version="entry-v1", permission=ToolPermission(
        authenticated=True, tenant_scoped=True), risk=RiskLevel.L0,
        side_effect="none", timeout_seconds=1, strict_validation=True)
    reg = ToolRegistry([Tool("entry_probe", "synthetic read", {"type": "object", "properties": {
        "value": {"type": "integer"}}, "required": ["value"]}, probe, contract)])

    async def tools(self):
        return reg

    monkeypatch.setattr(ChatService, "_build_tools", tools)
    monkeypatch.setattr(ChatService, "_maybe_reflect", lambda *args: None)
    with Session(db) as session:
        tenant = Tenant(name="entry acceptance", message_limit=1)
        session.add(tenant)
        session.commit()
        user = User(tenant_id=tenant.id, username="entry-member", hashed_password="synthetic", role="member")
        session.add(user)
        session.commit()
        session.refresh(user)
        session.expunge(user)
    api = FastAPI()
    api.include_router(router, prefix="/api/v1")

    async def current():
        log_context.set_user(user.id, user.tenant_id)
        return user

    def database():
        with Session(db) as session:
            yield session

    api.dependency_overrides[get_current_user] = current
    api.dependency_overrides[get_db] = database
    with TestClient(api) as client:
        yield client, db, user, provider, calls, control, reg
    set_llm_provider_override(None)
    db.dispose()


def checkpoint(db):
    with Session(db) as session:
        row = session.exec(select(AgentRunState)).one()
        return row.status, json.loads(row.state_json), row.message_id, row.conversation_id


@pytest.mark.parametrize("stream", [False, True])
def test_real_entry_success_contract(entry, stream):
    client, db, user, _, calls, _, _ = entry
    response = client.post("/api/v1/chat/stream" if stream else "/api/v1/chat",
                           json={"message": "please perform all three dependent steps"})
    assert response.status_code == 200, response.text
    status, snapshot, message_id, _ = checkpoint(db)
    assert calls == [1, 2, 3]
    assert status == "done" and snapshot["plan_cursor"] == 3
    assert [item["status"] for item in snapshot["plan_progress"]] == ["succeeded"] * 3
    if stream:
        assert "event: run.finished" in response.text and "event: error" not in response.text
        assert _terminal_status(response.text) == "done"
        assert "event: message" in response.text and "entry answer" in response.text
    else:
        assert response.json()["message_id"] == message_id
        assert response.json()["reply"] == "entry answer"
    with Session(db) as session:
        audits = session.exec(select(AuditLog).where(col(AuditLog.action) == "tool_call")).all()
        assert len(audits) == 3
        assert all(item.user_id == user.id and item.tenant_id == user.tenant_id for item in audits)


@pytest.mark.parametrize("stream", [False, True])
def test_real_entry_business_failure_locates_step(entry, stream):
    client, db, _, _, calls, control, _ = entry
    control["failure"] = True
    response = client.post("/api/v1/chat/stream" if stream else "/api/v1/chat",
                           json={"message": "please perform dependent steps and stop on failure"})
    assert response.status_code == 200
    status, snapshot, _, _ = checkpoint(db)
    assert calls == [1, 2]
    assert status == "error" and snapshot["failed_step_id"] == "step2"
    assert snapshot["plan_progress"][1]["error_code"] == "TOOL_FAILED"
    assert snapshot["plan_progress"][2]["status"] == "pending"
    assert "step2" in response.text and "TOOL_FAILED" in response.text
    if stream:
        assert "event: error" in response.text and "event: run.finished" in response.text
        assert _terminal_status(response.text) == "failed"


@pytest.mark.parametrize("refusal", ["forged_tenant", "L2_role"])
def test_real_entry_permission_refusal_audited(entry, refusal):
    client, db, user, provider, calls, _, reg = entry
    if refusal == "forged_tenant":
        provider.plan["steps"][0]["arguments"]["tenant_id"] = "foreign-tenant"
        reason = "forged_identity_argument"
    else:
        tool = reg.get("entry_probe")
        tool.contract = ToolContract(version="entry-L2-v1", permission=ToolPermission(
            authenticated=True, tenant_scoped=True, allowed_roles=frozenset({"system_admin"})),
            risk=RiskLevel.L2, side_effect="synthetic write", timeout_seconds=1, idempotent=False)
        reason = "role_not_allowed"
    response = client.post("/api/v1/chat", json={"message": "execute the requested dependent plan"})
    assert response.status_code == 200
    status, snapshot, _, _ = checkpoint(db)
    assert status == "error" and snapshot["failed_step_id"] == "step1"
    assert snapshot["plan_progress"][0]["error_code"] == "PERMISSION_DENIED"
    assert calls == []
    with Session(db) as session:
        audit = session.exec(select(AuditLog).where(col(AuditLog.action) == "tool_call")).one()
        details = json.loads(audit.details)
        assert audit.user_id == user.id and audit.tenant_id == user.tenant_id
        assert details["allowed"] is False and details["reason"] == reason
        assert set(details) == {"risk", "allowed", "reason", "latency_ms"}


@pytest.mark.parametrize("fault", ["unplanned", "timeout"])
@pytest.mark.parametrize("stream", [False, True])
def test_real_entry_reason_failure_has_no_tool_execution(entry, fault, stream):
    client, db, _, provider, calls, _, _ = entry
    provider.plan = entry_plan("reason")
    if fault == "unplanned":
        provider.reason = '<tool_call>{"name":"entry_probe","arguments":{"value":8}}</tool_call>'
    else:
        provider.reason_timeout = True
    response = client.post("/api/v1/chat/stream" if stream else "/api/v1/chat",
                           json={"message": "please perform the reasoning steps"})
    assert response.status_code == 200
    status, snapshot, _, _ = checkpoint(db)
    assert status == "error" and snapshot["failed_step_id"] == "step1"
    assert calls == []
    assert snapshot["plan_progress"][0]["error_code"] == (
        "UNPLANNED_TOOL_CALL" if fault == "unplanned" else "MODEL_TIMEOUT")
    assert snapshot["plan_progress"][1]["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_budget", [3, 1])
async def test_live_service_cancel_new_session_http_resume(entry, monkeypatch, resume_budget):
    client, db, user, provider, calls, _, _ = entry
    message = "please perform all dependent steps once"
    service = ChatService(llm_provider=provider)
    log_context.set_user(user.id, user.tenant_id)
    with Session(db) as session:
        iterator = service.chat_stream(session, user, message)
        tool_events = 0
        async for event in iterator:
            if event.type == "tool":
                tool_events += 1
                if tool_events == 2:
                    await iterator.aclose()
                    break
    status, snapshot, message_id, conversation_id = checkpoint(db)
    assert status == "running" and snapshot["plan_cursor"] == 1
    assert calls == [1] and snapshot["tool_rounds_used"] == 1
    monkeypatch.setattr(settings, "AGENT_MAX_TOOL_ROUNDS", resume_budget)
    response = client.post("/api/v1/chat/stream", json={"message": message,
                           "conversation_id": conversation_id, "resume_message_id": message_id})
    assert response.status_code == 200 and "event: run.finished" in response.text
    status, snapshot, _, _ = checkpoint(db)
    if resume_budget == 3:
        assert "event: error" not in response.text and calls == [1, 2, 3]
        assert _terminal_status(response.text) == "done"
        assert status == "done" and snapshot["tool_rounds_used"] == 3
    else:
        assert "event: error" in response.text and calls == [1]
        assert _terminal_status(response.text) == "failed"
        assert status == "error" and snapshot["tool_rounds_used"] == 1
        assert snapshot["failed_step_id"] == "step2"
        assert snapshot["plan_progress"][1]["error_code"] == "TOOL_BUDGET_EXCEEDED"
    assert provider.plans == 1
    with Session(db) as session:
        messages = session.exec(select(Message).where(col(Message.conversation_id) == conversation_id)).all()
        assert sum(item.role == "user" for item in messages) == 1
        assert session.get(Tenant, user.tenant_id).message_limit == 1


def test_explicit_text_rollback_keeps_real_entry_tool_behavior(entry, monkeypatch):
    client, db, _, _, calls, _, _ = entry
    original = AgentPipeline.__init__

    def rollback(self, *args, **kwargs):
        kwargs["structured_plans_enabled"] = False
        original(self, *args, **kwargs)

    monkeypatch.setattr(AgentPipeline, "__init__", rollback)
    response = client.post("/api/v1/chat", json={"message": "please use the legacy text plan"})
    assert response.status_code == 200 and response.json()["reply"] == "entry answer"
    status, snapshot, _, _ = checkpoint(db)
    assert status == "done" and snapshot["structured_plan"] is None
    assert calls == [9]
