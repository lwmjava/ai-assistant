"""Recovery boundaries exercised with real checkpoints and deterministic providers."""

import asyncio
import json
from datetime import timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.pipeline import AgentPipeline, AgentState, tool_fingerprint
from app.agents.state import StateManager
from app.agents.tools.base import Tool, ToolCall, ToolRegistry
from app.agents.tools.contract import RiskLevel, ToolContract, ToolPermission
from app.agents.tools.executor import ToolExecutor
from app.core.config import settings
from app.models.agent_state import AgentRunState
from app.models.conversation import Conversation, Message
from app.models.user import Tenant, User
from app.services.agent_state_service import AgentStateService, _utcnow_naive


class QuietRegistry(ToolRegistry):
    async def _record_audit(self, *args, **kwargs):
        pass


class StreamLLM:
    async def stream_chat(self, *args):
        yield "answer"


class ScriptPipeline(AgentPipeline):
    def __init__(self, replies=(), **kwargs):
        super().__init__(StreamLLM(), **kwargs)
        self.replies = iter(replies)

    async def _run_action_once(self, state):
        value = next(self.replies, "draft")
        if isinstance(value, BaseException):
            raise value
        return value

    async def _stage(self, *args):
        return "answer"

    async def _needs_plan(self, state):
        return False


def call(n):
    return '<tool_call>' + json.dumps({"name": "calculator", "arguments": {"n": n}}) + '</tool_call>'


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_STATE_PERSIST_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_STATE_CHECKPOINT_TOOL_ROUND", True)
    db = create_engine("sqlite://")
    SQLModel.metadata.create_all(db)
    with Session(db) as s:
        t = Tenant(name="recovery")
        s.add(t)
        s.commit()
        u = User(tenant_id=t.id, username="recovery", hashed_password="synthetic")
        s.add(u)
        s.commit()
        c = Conversation(tenant_id=t.id, user_id=u.id)
        s.add(c)
        s.commit()
        m = Message(conversation_id=c.id, role="user", content="synthetic")
        s.add(m)
        s.commit()
        binding = dict(conversation_id=c.id, message_id=m.id, tenant_id=t.id, user_id=u.id)
    yield db, binding
    db.dispose()


def store(session, binding):
    return AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])


def registry(calls):
    async def execute(args):
        calls.append(args["n"])
        return "result"
    return QuietRegistry([Tool("calculator", "synthetic", {"type": "object"}, execute)])


def test_live_checkpoint_resume_does_not_repeat_completed_tool(runtime):
    db, b = runtime
    calls = []
    reg = registry(calls)
    with Session(db) as s:
        p = ScriptPipeline([call(1), asyncio.CancelledError()], tools=reg, state_store=store(s, b), state_binding=b)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(p._run_action_loop(AgentState(user_input="synthetic")))
    with Session(db) as s:
        data = store(s, b).load_active(b["conversation_id"], b["message_id"])
        assert data["_round_no"] == 1
        assert data["executed_tool_fingerprints"] == [tool_fingerprint("calculator", {"n": 1})]
        p = ScriptPipeline([call(1), "draft"], tools=reg, state_store=store(s, b), state_binding=b)
        state = AgentState(user_input="synthetic")
        p._resume_state(state)
        asyncio.run(p._run_action_loop(state))
        assert calls == [1]


@pytest.mark.parametrize("used, exhausted, expected", [(2, False, [3]), (3, True, [])])
def test_restored_execution_keeps_total_budget(runtime, used, exhausted, expected):
    db, b = runtime
    calls = []
    with Session(db) as s:
        state = AgentState(user_input="synthetic")
        state.executed_tool_fingerprints = {tool_fingerprint("calculator", {"n": n}) for n in range(1, used + 1)}
        state.tool_budget_exhausted = exhausted
        store(s, b).save_checkpoint(**b, stage="action", round_no=used,
                                    tool_budget_exhausted=exhausted, state_json=StateManager(state).to_json(),
                                    status="running", error=None)
        p = ScriptPipeline([call(3), call(4), call(5)], tools=registry(calls), state_store=store(s, b), state_binding=b)
        p.max_tool_rounds = 3
        state = AgentState(user_input="synthetic")
        p._resume_state(state)
        asyncio.run(p._run_action_loop(state))
        assert calls == expected


@pytest.mark.parametrize("stream", [False, True])
def test_successful_short_path_has_terminal_checkpoint(runtime, stream):
    db, b = runtime
    with Session(db) as s:
        p = ScriptPipeline([call(1), "draft"], tools=registry([]), state_store=store(s, b), state_binding=b)
        state = AgentState(user_input="synthetic")
        if stream:
            async def collect():
                return [e.type async for e in p.run_stream(state)]
            assert "done" in asyncio.run(collect())
        else:
            assert asyncio.run(p.run(state)).answer == "answer"
        row = s.exec(select(AgentRunState)).one()
        assert row.status == "done"


@pytest.mark.parametrize("stream", [False, True])
def test_read_failure_falls_back_to_normal_execution(runtime, stream):
    _, b = runtime
    class BrokenStore:
        def load_active(self, *args):
            raise RuntimeError("synthetic database fault")
        def save_checkpoint(self, **kwargs):
            pass
    p = ScriptPipeline(state_store=BrokenStore(), state_binding=b)
    state = AgentState(user_input="synthetic")
    if stream:
        async def collect():
            return [e.type async for e in p.run_stream(state)]
        assert "done" in asyncio.run(collect())
    else:
        assert asyncio.run(p.run(state)).answer == "answer"
    assert state.error is None


@pytest.mark.parametrize("schema,value", [("object", 1), ("array", "bad"), ("integer", True), ("number", False)])
def test_strict_type_errors_never_call_function(schema, value):
    calls = []
    async def execute(args):
        calls.append(args)
        return "result"
    contract = ToolContract("1", ToolPermission(), RiskLevel.L0, "none", 1, strict_validation=True)
    tool = Tool("typed", "synthetic", {"properties": {"v": {"type": schema}}}, execute, contract)
    result = asyncio.run(ToolExecutor(QuietRegistry([tool])).execute(ToolCall("typed", {"v": value})))
    assert result.status == "invalid"
    assert calls == []


def test_malformed_snapshot_does_not_partially_mutate_state():
    state = AgentState(user_input="synthetic", plan="original")
    manager = StateManager.restore_from_json(state, '{"plan":"polluted","tool_results":null}')
    assert manager is None
    assert state.plan == "original"
    assert state.tool_results == []


def test_expired_cleanup_is_bounded_and_preserves_fresh_rows(runtime):
    db, b = runtime
    with Session(db) as s:
        for n in range(3):
            s.add(AgentRunState(tenant_id=b["tenant_id"], user_id=b["user_id"], conversation_id=b["conversation_id"],
                                expires_at=_utcnow_naive() - timedelta(seconds=10), status="done"))
        s.add(AgentRunState(tenant_id=b["tenant_id"], user_id=b["user_id"], conversation_id=b["conversation_id"],
                            message_id=b["message_id"], expires_at=_utcnow_naive() + timedelta(days=1)))
        s.commit()
        assert store(s, b).cleanup_expired(limit=2) == 2
        rows = s.exec(select(AgentRunState)).all()
        assert len(rows) == 2
        assert any(r.message_id == b["message_id"] for r in rows)


def test_invalid_intent_consumes_persisted_budget(runtime):
    db, b = runtime
    with Session(db) as s:
        p = ScriptPipeline(["<tool_call>bad</tool_call>", asyncio.CancelledError()],
                           tools=registry([]), state_store=store(s, b), state_binding=b)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(p._run_action_loop(AgentState(user_input="synthetic")))
    with Session(db) as s:
        data = store(s, b).load_active(b["conversation_id"], b["message_id"])
        assert data["_round_no"] == 1
        assert len(data["tool_results"]) == 1


@pytest.mark.asyncio
async def test_cleanup_lifecycle_runs_startup_and_periodic_batches(monkeypatch):
    from app.services import agent_state_cleanup as cleanup

    seen = []
    monkeypatch.setattr(settings, "AGENT_STATE_PERSIST_ENABLED", True)
    monkeypatch.setattr(cleanup, "_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(cleanup, "run_cleanup_batch", lambda: seen.append("batch") or 0)
    try:
        await cleanup.start_cleanup()
        task = cleanup._task
        await cleanup.start_cleanup()
        assert cleanup._task is task
        async with asyncio.timeout(2):
            while len(seen) < 2:
                await asyncio.sleep(0.01)
    finally:
        await cleanup.stop_cleanup()
    count = len(seen)
    await asyncio.sleep(0.03)
    assert len(seen) == count
    assert cleanup._task is None


@pytest.mark.parametrize("stream", [False, True])
def test_resume_rebuilds_current_retrieval_without_old_context(runtime, stream, monkeypatch):
    from app.rag.retrieval_status import RetrievalOutcome, RetrievalStatus
    from app.rag.vectorstore.base import ChunkResult
    from app.services.chat_service import ChatService

    monkeypatch.setattr(settings, "AGENT_QUALITY_GATE_ENABLED", False)
    db, b = runtime
    class CurrentRetriever:
        def __init__(self):
            self.calls = 0
            self.last_hits = []
            self.last_selected = []
        async def retrieve(self, query, plan):
            self.calls += 1
            self.last_hits = [ChunkResult("fresh", "CURRENT_EVIDENCE", "current.txt", "current-doc", 1.0)]
            self.last_outcome = RetrievalOutcome(RetrievalStatus.OK, self.last_hits)
            return "CURRENT_EVIDENCE"
    with Session(db) as s:
        old = AgentState(user_input="synthetic", context="REVOKED_OLD_EVIDENCE", plan="preserved plan")
        store(s, b).save_checkpoint(**b, stage="action", round_no=1, tool_budget_exhausted=False,
                                    state_json=StateManager(old).to_json(), status="running", error=None)
        retriever = CurrentRetriever()
        p = ScriptPipeline(["draft"], retriever=retriever, state_store=store(s, b), state_binding=b)
        state = AgentState(user_input="synthetic", context="CURRENT_MEMORY")
        if stream:
            async def collect():
                return [e.type async for e in p.run_stream(state)]
            assert "done" in asyncio.run(collect())
        else:
            asyncio.run(p.run(state))
        assert retriever.calls == 1
        assert "REVOKED_OLD_EVIDENCE" not in state.context
        assert "CURRENT_MEMORY" in state.context and "CURRENT_EVIDENCE" in state.context
        assert state.plan == "preserved plan"
        sources = ChatService._reply_sources(s, retriever)
        assert sources[0]["chunk_id"] == "fresh"
        assert sources[0]["filename"] == "current.txt"


def test_two_interruptions_do_not_refresh_remaining_budget(runtime):
    db, b = runtime
    calls = []
    for n in (1, 2):
        with Session(db) as s:
            p = ScriptPipeline([call(n), asyncio.CancelledError()], tools=registry(calls),
                               state_store=store(s, b), state_binding=b)
            p.max_tool_rounds = 3
            state = AgentState(user_input="synthetic")
            p._resume_state(state)
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(p._run_action_loop(state))
    with Session(db) as s:
        p = ScriptPipeline([call(3), call(4)], tools=registry(calls), state_store=store(s, b), state_binding=b)
        p.max_tool_rounds = 3
        state = AgentState(user_input="synthetic")
        p._resume_state(state)
        asyncio.run(p._run_action_loop(state))
        assert state.tool_budget_exhausted is True
    assert calls == [1, 2, 3]


def test_scoped_cleanup_keeps_other_tenant_rows_and_rolls_back_fault(runtime, monkeypatch):
    db, b = runtime
    with Session(db) as s:
        for tenant_id in (b["tenant_id"], "other"):
            s.add(AgentRunState(tenant_id=tenant_id, user_id=b["user_id"], conversation_id=b["conversation_id"],
                                expires_at=_utcnow_naive() - timedelta(days=1)))
        s.commit()
        original = s.execute
        def unavailable(*args, **kwargs):
            raise RuntimeError("synthetic delete unavailable")
        monkeypatch.setattr(s, "execute", unavailable)
        assert store(s, b).cleanup_expired() == 0
        monkeypatch.setattr(s, "execute", original)
        assert len(s.exec(select(AgentRunState)).all()) == 2
        assert store(s, b).cleanup_expired() == 1
        remaining = s.exec(select(AgentRunState)).one()
        assert remaining.tenant_id == "other"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("outcome", ["failure", "below_threshold", "normal"])
@pytest.mark.usefixtures("legacy_plan_mode")
def test_full_pipeline_terminal_status_for_all_exits(runtime, stream, outcome, monkeypatch):
    from app.rag.retrieval_status import RetrievalOutcome, RetrievalStatus

    monkeypatch.setattr(settings, "AGENT_QUALITY_GATE_ENABLED", False)
    monkeypatch.setattr(settings, "RAG_STATUS_NOTICE_ENABLED", True)
    db, b = runtime
    class FullPipeline(ScriptPipeline):
        async def _needs_plan(self, state):
            return True
        async def _stage(self, *args):
            if outcome == "failure":
                raise RuntimeError("synthetic provider unavailable")
            return "answer"
    class LowRetriever:
        last_outcome = RetrievalOutcome(RetrievalStatus.BELOW_THRESHOLD)
        last_hits = []
        async def retrieve(self, *args):
            return ""
    with Session(db) as s:
        p = FullPipeline(retriever=LowRetriever() if outcome == "below_threshold" else None,
                         state_store=store(s, b), state_binding=b)
        state = AgentState(user_input="synthetic")
        if stream:
            async def collect():
                return [e.type async for e in p.run_stream(state)]
            events = asyncio.run(collect())
            assert events[-1] == "done"
        else:
            asyncio.run(p.run(state))
        row = s.exec(select(AgentRunState)).one()
        assert row.status == ("error" if outcome == "failure" else "done")
        if outcome == "below_threshold":
            assert state.answer == settings.RAG_REFUSE_MESSAGE
