"""Isolated, deterministic review probes; no network or production database access."""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["ENV"] = "development"
os.environ["DATABASE_URL"] = "sqlite:///./data/test_ai_assistant.db"

from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.pipeline import AgentPipeline, AgentState, tool_fingerprint
from app.agents.state import StateManager
from app.agents.tools.base import Tool, ToolCall, ToolRegistry
from app.agents.tools.contract import RiskLevel, ToolContract, ToolPermission
from app.agents.tools.executor import ToolExecutor
from app.core.config import settings
from app.models.agent_state import AgentRunState
from app.models.user import Tenant, User
from app.services.agent_state_service import AgentStateService
from app.services.quota import accept_user_message


class NoAuditRegistry(ToolRegistry):
    async def _record_audit(self, *args, **kwargs):
        pass


class ScriptPipeline(AgentPipeline):
    def __init__(self, replies, **kwargs):
        super().__init__(None, **kwargs)
        self.replies = iter(replies)

    async def _run_action_once(self, state):
        reply = next(self.replies, "draft")
        if isinstance(reply, BaseException):
            raise reply
        return reply

    async def _stage(self, *args):
        return "draft"

    async def _needs_plan(self, state):
        return False


class StreamLLM:
    async def stream_chat(self, *args):
        yield "answer"


def call(n):
    return '<tool_call>' + json.dumps({"name": "calculator", "arguments": {"n": n}}) + '</tool_call>'


async def main():
    settings.AGENT_STATE_PERSIST_ENABLED = True
    settings.AGENT_STATE_CHECKPOINT_TOOL_ROUND = True
    db = create_engine("sqlite://")
    SQLModel.metadata.create_all(db)
    binding = {"conversation_id": "synthetic-conv", "message_id": "synthetic-msg",
               "tenant_id": "synthetic-tenant", "user_id": "synthetic-user"}
    calls = []

    async def tool(arguments):
        calls.append(arguments["n"])
        return "result"

    registry = NoAuditRegistry([Tool("calculator", "synthetic", {"type": "object"}, tool)])
    result = {}
    with Session(db) as session:
        store = AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        p = ScriptPipeline([call(1), asyncio.CancelledError()], tools=registry,
                           state_store=store, state_binding=binding)
        try:
            await p._run_action_loop(AgentState(user_input="synthetic"))
        except asyncio.CancelledError:
            pass
    with Session(db) as session:
        row = session.exec(select(AgentRunState)).one()
        snap = json.loads(row.state_json)
        result["checkpoint_after_completed_tool"] = {
            "tool_results_count": len(snap["tool_results"]),
            "fingerprints": snap["executed_tool_fingerprints"], "round_no": row.round_no}
        assert len(snap["tool_results"]) == 1 and snap["executed_tool_fingerprints"] == []
        store = AgentStateService(session, tenant_id=binding["tenant_id"], user_id=binding["user_id"])
        p = ScriptPipeline([call(1), "draft"], tools=registry, state_store=store, state_binding=binding)
        state = AgentState(user_input="synthetic")
        p._resume_state(state)
        await p._run_action_loop(state)
        result["repeat_after_resume"] = list(calls)
        assert calls == [1, 1]

        state = AgentState(user_input="synthetic")
        state.executed_tool_fingerprints = {tool_fingerprint("calculator", {"n": 1}),
                                            tool_fingerprint("calculator", {"n": 2})}
        store.save_checkpoint(**binding, stage="action", round_no=2, tool_budget_exhausted=False,
                              state_json=StateManager(state).to_json(), status="running", error=None)
        calls.clear()
        p = ScriptPipeline([call(3), call(4), call(5), "draft"], tools=registry,
                           state_store=store, state_binding=binding)
        p.max_tool_rounds = 3
        state = AgentState(user_input="synthetic")
        p._resume_state(state)
        await p._run_action_loop(state)
        result["budget_after_resume"] = {"limit": 3, "prior_rounds": 2, "new_calls": list(calls)}
        assert calls == [3, 4, 5]

        store.discard(binding["conversation_id"], binding["message_id"])
        p = ScriptPipeline([call(6), "draft"], tools=registry, state_store=store, state_binding=binding)
        await p.run(AgentState(user_input="synthetic"))
        row = session.exec(select(AgentRunState)).one()
        result["successful_short_run_status"] = row.status
        assert row.status == "running"
        store.discard(binding["conversation_id"], binding["message_id"])
        p = ScriptPipeline([call(7), "draft"], tools=registry, state_store=store, state_binding=binding)
        p.llm = StreamLLM()
        events = [event.type async for event in p.run_stream(AgentState(user_input="synthetic"))]
        row = session.exec(select(AgentRunState)).one()
        result["successful_short_stream"] = {"status": row.status, "events": events}
        assert "done" in events and row.status == "running"

    class BrokenRead:
        def load_active(self, *args):
            raise RuntimeError("synthetic read failure")

    p = ScriptPipeline([], state_store=BrokenRead(), state_binding=binding)
    try:
        _ = [event async for event in p.run_stream(AgentState(user_input="synthetic"))]
    except RuntimeError:
        result["stream_read_failure_escapes"] = True
    else:
        raise AssertionError("Expected uncaught read error")

    count = []
    async def strict_tool(arguments):
        count.append(arguments)
        return "result"

    contract = ToolContract(version="synthetic", permission=ToolPermission(), risk=RiskLevel.L0,
                            side_effect="none", timeout_seconds=1, strict_validation=True)
    t = Tool("strict", "synthetic", {"type": "object", "properties": {
        "obj": {"type": "object"}, "arr": {"type": "array"}, "num": {"type": "integer"}}},
        strict_tool, contract)
    e = await ToolExecutor(NoAuditRegistry([t])).execute(ToolCall("strict", {"obj": 1, "arr": "bad", "num": True}))
    result["strict_invalid_types"] = {"status": e.status, "function_calls": len(count)}
    assert e.status == "ok" and len(count) == 1

    with Session(db) as session:
        tenant = Tenant(name="synthetic-review")
        session.add(tenant)
        session.commit()
        user = User(tenant_id=tenant.id, username="synthetic-review", hashed_password="synthetic")
        session.add(user)
        session.commit()
        identity = {"tenant_id": tenant.id, "user_id": user.id, "role": "member"}
        conv, first_id = accept_user_message(session, **identity, conversation_id=None, message="same request")
        conv_id = conv.id
        svc = AgentStateService(session, tenant_id=tenant.id, user_id=user.id)
        svc.save_checkpoint(conversation_id=conv_id, message_id=first_id,
                            tenant_id=tenant.id, user_id=user.id, stage="action", round_no=1,
                            tool_budget_exhausted=False, state_json='{"plan":"checkpoint"}',
                            status="running", error=None)
        _, retry_id = accept_user_message(session, **identity, conversation_id=conv_id, message="same request")
        result["chat_retry_binding"] = {"new_message_id": first_id != retry_id,
                                        "retry_load_found": svc.load_active(conv_id, retry_id) is not None,
                                        "original_load_found": svc.load_active(conv_id, first_id) is not None}
        assert first_id != retry_id and svc.load_active(conv_id, retry_id) is None
    text = json.dumps(result, indent=2, ensure_ascii=False)
    Path(__file__).with_suffix(".json").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
