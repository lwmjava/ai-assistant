from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.database import get_session
from app.core.security import create_access_token
from app.debug.trace import AgentTrace
from app.main import app
from app.models.run_history import RunHistory
from app.models.user import Tenant, User
from app.services.run_history import RunHistoryAccessError, RunHistoryService


@pytest.fixture
def context():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        users = {
            "owner": User(id="owner", tenant_id="a", username="owner", hashed_password="", role="member"),
            "admin": User(id="admin", tenant_id="a", username="admin", hashed_password="", role="tenant_admin"),
            "peer": User(id="peer", tenant_id="a", username="peer", hashed_password="", role="member"),
            "other": User(id="other", tenant_id="b", username="other", hashed_password="", role="tenant_admin"),
        }
        yield session, users
    engine.dispose()


def traced() -> AgentTrace:
    trace = AgentTrace(run_id="run-1", debug_mode=True)
    trace.start()
    trace.stage_start("规划", secret="must-not-persist")
    trace.llm_call("mock", prompt="private prompt", response="private response", latency_ms=4)
    trace.tool_call("search", args={"token": "secret"}, result="private", latency_ms=3)
    trace.stage_end("规划")
    trace.finish()
    return trace


def test_persistence_redacts_payloads_and_keeps_unknown_usage(context):
    session, users = context
    row = RunHistoryService(session).record(users["owner"], "conversation-1", traced())
    assert row.input_tokens is row.output_tokens is None
    assert row.usage_source is None
    for secret in ("must-not-persist", "private prompt", "private response", "secret"):
        assert secret not in row.events_json
    detail = RunHistoryService(session).get(users["owner"], row.id)
    assert detail.llm_call_count == detail.tool_call_count == 1
    assert any(event.duration_ms is not None for event in detail.events)


@pytest.mark.parametrize("debug_mode", [False, True])
def test_call_counts_and_latency_persist_without_verbose_events(context, debug_mode):
    session, users = context
    trace = AgentTrace(debug_mode=debug_mode)
    trace.start()
    trace.llm_call("mock", prompt="private prompt", response="private answer", latency_ms=4)
    trace.llm_call("mock", latency_ms=6)
    trace.tool_call("search", args={"token": "private token"}, result="private result", latency_ms=3)
    trace.finish()
    summary = trace.summary()
    assert summary["llm_call_count"] == 2
    assert summary["tool_call_count"] == 1
    assert summary["total_llm_latency_ms"] == 10
    assert summary["total_tool_latency_ms"] == 3
    if not debug_mode:
        assert trace.events == []
    row = RunHistoryService(session).record(users["owner"], "conversation-1", trace)
    assert row.llm_call_count == 2 and row.tool_call_count == 1
    assert row.input_tokens is row.output_tokens is None
    assert "private" not in row.events_json
    if not debug_mode:
        assert row.events_json == "[]"


def test_call_counts_survive_debug_toggle_and_summary_reads():
    trace = AgentTrace()
    trace.llm_call("mock", latency_ms=1)
    assert trace.summary()["llm_call_count"] == 1
    trace.debug_mode = True
    trace.llm_call("mock", latency_ms=2)
    trace.tool_call("search", latency_ms=3)
    trace.debug_mode = False
    trace.tool_call("search", latency_ms=4)
    assert trace.summary() == trace.summary()
    assert trace.summary()["llm_call_count"] == trace.summary()["tool_call_count"] == 2
    assert len(trace.events) == 2


@pytest.mark.parametrize("fast_path", [False, True])
async def test_chat_stream_close_persists_final_model_attempt(context, monkeypatch, fast_path):
    from app.agents.pipeline import AgentPipeline
    from app.core.config import settings
    from app.llm.mock import MockLLMProvider
    from app.services.chat_service import ChatService

    class CountingModel(MockLLMProvider):
        calls = 0

        async def chat(self, messages, options=None):
            self.calls += 1
            return "YES"

        async def stream_chat(self, messages, options=None):
            yield await self.chat(messages, options)

    session, users = context
    session.add(Tenant(id="a", name="A"))
    session.add(users["owner"])
    session.commit()
    for name in ("MEMORY_ENABLED", "SKILL_ENABLED", "MCP_ENABLED", "EVOLUTION_ENABLED",
                 "LLM_RESPONSE_CACHE_ENABLED", "AGENT_STATE_PERSIST_ENABLED"):
        monkeypatch.setattr(settings, name, False)
    model = CountingModel()
    trace = AgentTrace()
    service = ChatService(model)
    if not fast_path:
        monkeypatch.setattr(service, "_fast_route", lambda message: None)
    else:
        monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "langgraph")
    monkeypatch.setattr(service, "_create_trace", lambda: trace)
    monkeypatch.setattr(service, "_build_retriever", lambda *args: None)
    monkeypatch.setattr(service, "_build_pipeline", lambda *args, **kwargs:
                        AgentPipeline(model, trace=trace, structured_plans_enabled=False))
    stream = service.chat_stream(session, users["owner"], "synthetic question")
    async for event in stream:
        if event.type == "token":
            break
    else:
        pytest.fail("chat did not produce a token")
    await stream.aclose()
    row = RunHistoryService(session).get(users["owner"], trace.run_id)
    assert row.llm_call_count == model.calls
    assert row.events == []
    assert row.status == "failed"
    assert row.error_code == "run_cancelled"


def test_owner_and_same_tenant_admin_visibility_without_disclosure(context):
    session, users = context
    service = RunHistoryService(session)
    row = service.record(users["owner"], "conversation-1", traced())
    assert service.list(users["owner"])[1] == 1
    assert service.list(users["admin"])[1] == 1
    assert service.list(users["peer"])[1] == 0
    for principal in (users["peer"], users["other"]):
        with pytest.raises(RunHistoryAccessError):
            service.get(principal, row.id)
        with pytest.raises(RunHistoryAccessError):
            service.get(principal, "missing")


def test_expired_rows_are_hidden_not_deleted(context):
    session, users = context
    service = RunHistoryService(session)
    row = service.record(users["owner"], "conversation-1", traced())
    row.created_at = datetime.now(UTC) - timedelta(days=31)
    session.add(row)
    session.commit()
    assert service.list(users["owner"])[1] == 0
    assert session.get(RunHistory, row.id) is not None
    with pytest.raises(RunHistoryAccessError):
        service.get(users["owner"], row.id)


def test_trace_persistence_failure_rolls_back_chat_session():
    from app.services.chat_service import ChatService

    session = Mock()
    user = User(id="owner", tenant_id="a", username="owner", hashed_password="")
    with patch.object(RunHistoryService, "record", side_effect=RuntimeError("database unavailable")):
        ChatService._collect_trace(session, user, "conversation-1", traced())
    session.rollback.assert_called_once_with()


def test_api_authorization_and_non_disclosure():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="a", name="A"))
        session.add(Tenant(id="b", name="B"))
        owner = User(id="owner", tenant_id="a", username="owner", hashed_password="", role="member")
        session.add(owner)
        session.add(User(id="admin", tenant_id="a", username="admin", hashed_password="", role="tenant_admin"))
        session.add(User(id="other", tenant_id="b", username="other", hashed_password="", role="tenant_admin"))
        session.add(User(id="platform", tenant_id="a", username="platform", hashed_password="", role="system_admin"))
        session.commit()
        RunHistoryService(session).record(owner, "conversation-1", traced())

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = database
    client = TestClient(app)
    headers = {
        key: {"Authorization": f"Bearer {create_access_token(key, tenant, role)}"}
        for key, tenant, role in (
            ("owner", "a", "member"),
            ("admin", "a", "tenant_admin"),
            ("other", "b", "tenant_admin"),
            ("platform", "a", "system_admin"),
        )
    }
    try:
        assert client.get("/api/v1/run-history", headers=headers["platform"]).json()["total"] == 1
        assert client.get("/api/v1/run-history", headers=headers["owner"]).status_code == 403
        assert client.get("/api/v1/run-history", headers=headers["admin"]).status_code == 403
        hidden = client.get("/api/v1/run-history/run-1", headers=headers["other"])
        missing = client.get("/api/v1/run-history/missing", headers=headers["other"])
        assert hidden.status_code == missing.status_code == 403
        hidden = client.get("/api/v1/run-history/other-tenant", headers=headers["platform"])
        missing = client.get("/api/v1/run-history/missing", headers=headers["platform"])
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json() == missing.json()
        assert client.get("/api/v1/run-history").status_code == 401
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()
