"""HTTP recovery reuses the owned message and refuses unsafe bindings."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.agents.pipeline import AgentState, tool_fingerprint
from app.agents.state import StateManager
from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import engine
from app.main import app
from app.models.agent_state import AgentRunState
from app.models.conversation import Conversation, Message
from app.models.user import Tenant, User
from app.services.agent_state_service import AgentStateService, _utcnow_naive
from app.services.chat_service import ChatService
from tests.test_agent_runtime_recovery import ScriptPipeline, call, registry


def _terminal_status(body):
    for frame in body.replace("\r\n", "\n").split("\n\n"):
        if "event: run.finished\n" in frame:
            data = next(line[5:].strip() for line in frame.splitlines() if line.startswith("data:"))
            return json.loads(data)["status"]
    raise AssertionError("缺少运行终态")


@pytest.fixture
def resume_client(monkeypatch):
    monkeypatch.setattr(settings, "RAG_ENABLED", False)
    monkeypatch.setattr(settings, "AGENT_STATE_PERSIST_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self")
    calls = []
    reg = registry(calls)
    async def tools(self):
        return reg
    monkeypatch.setattr(ChatService, "_build_tools", tools)
    monkeypatch.setattr(
        ChatService, "_build_pipeline", lambda self, *a, **kw: ScriptPipeline([call(1), "draft"], tools=reg)
    )
    monkeypatch.setattr(ChatService, "_maybe_reflect", lambda *args: None)
    with Session(engine) as s:
        tenant = Tenant(name="resume", message_limit=1)
        s.add(tenant)
        s.commit()
        user = User(tenant_id=tenant.id, username="resume-" + tenant.id, hashed_password="synthetic")
        s.add(user)
        s.commit()
        s.refresh(user)
        conv = Conversation(tenant_id=tenant.id, user_id=user.id)
        s.add(conv)
        s.commit()
        msg = Message(conversation_id=conv.id, role="user", content="synthetic resume")
        s.add(msg)
        s.commit()
        state = AgentState(user_input=msg.content, needs_full_pipeline=False)
        state.tool_results = ["completed result"]
        state.executed_tool_fingerprints = {tool_fingerprint("calculator", {"n": 1})}
        AgentStateService(s, tenant_id=user.tenant_id, user_id=user.id).save_checkpoint(
            conversation_id=conv.id, message_id=msg.id, tenant_id=user.tenant_id, user_id=user.id,
            stage="action", round_no=1, tool_budget_exhausted=False,
            state_json=StateManager(state).to_json(), status="running", error=None)
        payload = {"message": msg.content, "conversation_id": conv.id, "resume_message_id": msg.id}
        app.dependency_overrides[get_current_user] = lambda: user
        with TestClient(app) as client:
            yield client, payload, user, calls
        app.dependency_overrides.clear()
        for row in s.exec(select(AgentRunState).where(col(AgentRunState.conversation_id) == conv.id)).all():
            s.delete(row)
        for row in s.exec(select(Message).where(col(Message.conversation_id) == conv.id)).all():
            s.delete(row)
        s.delete(conv)
        s.delete(user)
        s.delete(tenant)
        s.commit()


@pytest.mark.parametrize("stream", [False, True])
def test_http_resume_reuses_message_without_quota_charge(resume_client, stream):
    client, payload, _, calls = resume_client
    resp = client.post("/api/v1/chat/stream" if stream else "/api/v1/chat", json=payload)
    assert resp.status_code == 200, resp.text
    if stream:
        assert "event: run.finished" in resp.text
        assert _terminal_status(resp.text) == "done"
        assert "event: message" in resp.text
    else:
        assert resp.json()["message_id"] == payload["resume_message_id"]
        assert resp.json()["reply"] == "answer"
    with Session(engine) as s:
        messages = s.exec(select(Message).where(col(Message.conversation_id) == payload["conversation_id"])).all()
        assert len([m for m in messages if m.role == "user"]) == 1
        row = s.exec(select(AgentRunState).where(col(AgentRunState.message_id) == payload["resume_message_id"])).one()
        assert row.status == "done"
    assert calls == []


@pytest.mark.parametrize("change", [
    "other_user", "other_tenant", "wrong_message", "wrong_content", "done", "expired", "disabled",
    "missing_conversation", "corrupt", "negative_round",
])
def test_http_resume_rejects_invalid_binding(resume_client, monkeypatch, change):
    client, payload, user, calls = resume_client
    if change in {"other_user", "other_tenant"}:
        actor = User(id="other", tenant_id=user.tenant_id if change == "other_user" else "other-tenant",
                     username="other", hashed_password="synthetic", role="system_admin")
        app.dependency_overrides[get_current_user] = lambda: actor
    elif change == "wrong_message":
        payload["resume_message_id"] = "missing"
    elif change == "wrong_content":
        payload["message"] = "different"
    elif change == "missing_conversation":
        payload.pop("conversation_id")
    elif change == "disabled":
        monkeypatch.setattr(settings, "AGENT_STATE_PERSIST_ENABLED", False)
    else:
        with Session(engine) as s:
            row = s.exec(select(AgentRunState).where(
                col(AgentRunState.message_id) == payload["resume_message_id"])).one()
            if change == "done":
                row.status = "done"
            elif change == "expired":
                row.expires_at = _utcnow_naive() - timedelta(seconds=1)
            elif change == "negative_round":
                row.round_no = -1
            else:
                row.state_json = json.dumps({"tool_results": None})
            s.add(row)
            s.commit()
    resp = client.post("/api/v1/chat", json=payload)
    assert resp.status_code in {400, 404, 409, 422}
    assert calls == []
    with Session(engine) as s:
        rows = s.exec(select(Message).where(col(Message.conversation_id) == payload.get("conversation_id", ""))).all()
        assert not any(m.role == "assistant" for m in rows)


@pytest.mark.asyncio
async def test_active_stream_blocks_resume_and_close_releases_claim(resume_client):
    client, payload, user, _ = resume_client
    with Session(engine) as s:
        stream = ChatService().chat_stream(
            s, user, payload["message"], payload["conversation_id"],
            resume_message_id=payload["resume_message_id"])
        first = await anext(stream)
        assert first.type == "conversation"
        denied = client.post("/api/v1/chat/stream", json=payload)
        assert denied.status_code == 409
        await stream.aclose()
    assert client.post("/api/v1/chat", json=payload).status_code == 200


@pytest.mark.asyncio
async def test_cancelled_live_stream_can_resume_without_repeating_tool(resume_client, monkeypatch):
    import asyncio

    client, payload, user, calls = resume_client
    reg = registry(calls)
    monkeypatch.setattr(ChatService, "_build_pipeline", lambda self, *a, **kw: ScriptPipeline(
        [call(2), asyncio.CancelledError()], tools=reg))
    with Session(engine) as s:
        stream = ChatService().chat_stream(
            s, user, payload["message"], payload["conversation_id"],
            resume_message_id=payload["resume_message_id"])
        with pytest.raises(asyncio.CancelledError):
            async for _ in stream:
                pass
        await stream.aclose()
    assert calls == [2]
    with Session(engine) as s:
        row = s.exec(select(AgentRunState).where(
            col(AgentRunState.message_id) == payload["resume_message_id"])).one()
        assert row.status == "running"
        assert row.round_no == 2
    monkeypatch.setattr(ChatService, "_build_pipeline", lambda self, *a, **kw: ScriptPipeline(
        [call(2), "draft"], tools=reg))
    resumed = client.post("/api/v1/chat/stream", json=payload)
    assert resumed.status_code == 200 and "event: run.finished" in resumed.text
    assert _terminal_status(resumed.text) == "done"
    assert calls == [2]


def test_resume_contract_is_documented_in_openapi():
    schema = app.openapi()["components"]["schemas"]
    assert "resume_message_id" in schema["ChatRequest"]["properties"]
    assert "message_id" in schema["ChatResponse"]["properties"]
