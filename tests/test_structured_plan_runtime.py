"""Plan behavior at real Pipeline entry points (synthetic model, real executor)."""

import asyncio
import json

import pytest
from sqlmodel import Session

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.prompts import SYSTEM_ACT, SYSTEM_PLAN, SYSTEM_PREFLOW
from app.agents.state import StateManager
from app.agents.tools.base import Tool, ToolRegistry
from app.agents.tools.builtin import default_tools
from app.agents.tools.executor import ToolExecutor
from app.core.config import settings
from app.llm.base import LLMOptions, LLMProvider
from tests.test_agent_runtime_recovery import runtime as recovery_runtime
from tests.test_agent_runtime_recovery import store


def plan_text():
    return json.dumps({"schema_version": 1, "steps": [
        {"id": "first", "description": "calculate", "kind": "tool", "tool": "echo",
         "arguments": {"value": 1}, "depends_on": [], "expected_output": "first value"},
        {"id": "second", "description": "calculate again", "kind": "tool", "tool": "echo",
         "arguments": {"value": 2}, "depends_on": ["first"], "expected_output": "second value"},
    ]})


class PlanLLM(LLMProvider):
    model = "plan-script-v1"

    def __init__(self, plan=None):
        self.plan = plan if plan is not None else plan_text()

    async def chat(self, messages, options=None):
        system = messages[0].content
        if system == SYSTEM_PREFLOW:
            return "YES"
        if system == SYSTEM_PLAN:
            return self.plan
        if system == SYSTEM_ACT:
            return "draft"
        return "answer"

    async def stream_chat(self, messages, options=None):
        yield await self.chat(messages, options)


class QuietRegistry(ToolRegistry):
    async def _record_audit(self, *args, **kwargs):
        pass


@pytest.fixture(autouse=True)
def no_quality_gate(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_QUALITY_GATE_ENABLED", False)


@pytest.fixture
def runtime(monkeypatch):
    yield from recovery_runtime.__wrapped__(monkeypatch)


def registry(calls, fail=False):
    async def echo(args):
        calls.append(args["value"])
        if fail and args["value"] == 1:
            raise ConnectionError("synthetic upstream unavailable")
        return str(args["value"])
    return QuietRegistry([Tool("echo", "synthetic", {"type": "object", "properties": {
        "value": {"type": "integer"}}, "required": ["value"]}, echo)])


def test_plan_declared_steps_execute_in_order():
    calls = []
    state = asyncio.run(AgentPipeline(PlanLLM(), tools=registry(calls)).run(AgentState(user_input="multi step")))
    assert calls == [1, 2]
    assert state.structured_plan["steps"][1]["depends_on"] == ["first"]
    assert [item["status"] for item in state.plan_progress] == ["succeeded", "succeeded"]


def test_plan_failure_identifies_step_and_stops_successor():
    calls = []
    state = asyncio.run(AgentPipeline(PlanLLM(), tools=registry(calls, fail=True)).run(
        AgentState(user_input="multi step")))
    assert calls == [1]
    assert state.failed_step_id == "first"
    assert state.plan_progress[0]["status"] == "failed"
    assert state.plan_progress[1]["status"] == "pending"
    assert state.error


def test_plan_malformed_new_plan_never_executes():
    calls = []
    state = asyncio.run(AgentPipeline(PlanLLM('{"schema_version":1,"steps":[]}'), tools=registry(calls)).run(
        AgentState(user_input="multi step")))
    assert state.error
    assert calls == []


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_plan_same_step_status_at_both_entries(stream, failure):
    calls = []
    p = AgentPipeline(PlanLLM(), tools=registry(calls, failure))
    state = AgentState(user_input="multi step")
    async def go():
        if stream:
            return [event.type async for event in p.run_stream(state)]
        await p.run(state)
        return []
    events = asyncio.run(go())
    assert calls == ([1] if failure else [1, 2])
    assert state.plan_cursor == (0 if failure else 2)
    assert state.plan_progress[0]["status"] == ("failed" if failure else "succeeded")
    if stream:
        assert events[-1] == "done"
        assert ("error" in events) == failure


def test_plan_cancel_then_new_session_resume_skips_completed_prefix(runtime):
    db, binding = runtime
    calls = []
    reg = registry(calls)
    async def interrupt():
        with Session(db) as session:
            p = AgentPipeline(PlanLLM(), tools=reg, state_store=store(session, binding), state_binding=binding)
            state = AgentState(user_input="multi step")
            iterator = p.run_stream(state)
            seen = 0
            async for event in iterator:
                if event.type == "tool":
                    seen += 1
                    if seen == 2:
                        await iterator.aclose()
                        break
    asyncio.run(interrupt())
    assert calls == [1]
    with Session(db) as session:
        snap = store(session, binding).load_active(binding["conversation_id"], binding["message_id"])
        assert snap["plan_cursor"] == 1
        assert snap["tool_rounds_used"] == 1
        p = AgentPipeline(PlanLLM("malformed unused"), tools=reg,
                          state_store=store(session, binding), state_binding=binding,
                          structured_plans_enabled=False)
        result = asyncio.run(p.run(AgentState(user_input="multi step")))
        assert calls == [1, 2]
        assert result.plan_cursor == 2
        assert result.tool_rounds_used == 2
        assert store(session, binding).load_active(binding["conversation_id"], binding["message_id"]) is None


def test_plan_corrupt_progress_rejected_without_partial_mutation():
    state = AgentState(user_input="multi step", plan=plan_text())
    p = AgentPipeline(PlanLLM(), tools=registry([]))
    p._prepare_structured_plan(state)
    data = StateManager(state).snapshot()
    data["plan_cursor"] = 1
    assert not StateManager.valid_snapshot(data)
    target = AgentState(user_input="same", plan="original")
    assert not StateManager(target).apply_snapshot(data)
    assert target.plan == "original"


def test_plan_budget_limits_before_successor():
    calls = []
    p = AgentPipeline(PlanLLM(), tools=registry(calls))
    p.max_tool_rounds = 1
    state = asyncio.run(p.run(AgentState(user_input="multi step")))
    assert calls == [1]
    assert state.failed_step_id == "second"
    assert state.plan_progress[1]["error_code"] == "TOOL_BUDGET_EXCEEDED"


@pytest.mark.parametrize("stream", [False, True])
def test_plan_real_business_failure_never_reports_step_success(stream):
    data = json.loads(plan_text())
    data["steps"][0]["tool"] = "calculator"
    data["steps"][0]["arguments"] = {"expression": "1/0"}
    data["steps"][1]["tool"] = "get_current_datetime"
    data["steps"][1]["arguments"] = {}
    reg = QuietRegistry(default_tools())
    state = AgentState(user_input="multi step")
    p = AgentPipeline(PlanLLM(json.dumps(data)), tools=reg, executor=ToolExecutor(reg))
    async def go():
        if stream:
            return [e.type async for e in p.run_stream(state)]
        await p.run(state)
    asyncio.run(go())
    assert state.plan_progress[0]["status"] == "failed"
    assert state.plan_progress[1]["status"] == "pending"
    assert state.failed_step_id == "first"


def test_plan_quality_gate_never_reexecutes_tools(monkeypatch):
    from app.agents.prompts import SYSTEM_QUALITY_GATE

    class LowQualityLLM(PlanLLM):
        async def chat(self, messages, options=None):
            if messages[0].content == SYSTEM_QUALITY_GATE:
                return "0.1"
            return await super().chat(messages, options)
    monkeypatch.setattr(settings, "AGENT_QUALITY_GATE_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_MAX_REVISIONS", 2)
    calls = []
    state = asyncio.run(AgentPipeline(LowQualityLLM(), tools=registry(calls)).run(AgentState(user_input="multi step")))
    assert not state.error
    assert calls == [1, 2]
    assert state.tool_rounds_used == 2


@pytest.mark.parametrize("stream", [False, True])
def test_plan_reason_timeout_is_step_failure(stream):
    class SlowReasonLLM(PlanLLM):
        async def chat(self, messages, options=None):
            if messages[0].content.startswith("只完成当前推理步骤"):
                await asyncio.sleep(0.2)
            return await super().chat(messages, options)
    text = json.dumps({"schema_version": 1, "steps": [{"id": "reason1", "kind": "reason",
        "description": "reason", "depends_on": [], "expected_output": "answer"}]})
    state = AgentState(user_input="multi step")
    p = AgentPipeline(SlowReasonLLM(text), options=LLMOptions(timeout=0.01))
    async def go():
        if stream:
            return [e.type async for e in p.run_stream(state)]
        await p.run(state)
    asyncio.run(go())
    assert state.failed_step_id == "reason1"
    assert state.plan_progress[0]["error_code"] == "MODEL_TIMEOUT"
    assert state.error


def test_plan_final_stream_timeout_records_error_checkpoint(runtime):
    class SlowResponseLLM(PlanLLM):
        async def stream_chat(self, messages, options=None):
            await asyncio.sleep(0.2)
            yield "too late"
    db, b = runtime
    with Session(db) as session:
        state = AgentState(user_input="multi step")
        p = AgentPipeline(SlowResponseLLM(), tools=registry([]), options=LLMOptions(timeout=0.01),
                          state_store=store(session, b), state_binding=b)
        async def go():
            return [e.type async for e in p.run_stream(state)]
        events = asyncio.run(go())
        assert "error" in events
        assert state.error == "TimeoutError"
        from sqlmodel import select

        from app.models.agent_state import AgentRunState

        row = session.exec(select(AgentRunState)).one()
        assert row.status == "error"


def test_plan_rejected_credentials_never_persist_in_terminal_snapshot(runtime):
    from sqlmodel import select

    from app.models.agent_state import AgentRunState

    db, b = runtime
    data = json.loads(plan_text())
    data["steps"][0]["arguments"]["password"] = "SYNTHETIC_PLAN_PASSWORD_MARKER"
    calls = []
    with Session(db) as session:
        state = asyncio.run(AgentPipeline(PlanLLM(json.dumps(data)), tools=registry(calls),
            state_store=store(session, b), state_binding=b).run(AgentState(user_input="multi step")))
        assert state.error
    with Session(db) as session:
        row = session.exec(select(AgentRunState)).one()
        assert "SYNTHETIC_PLAN_PASSWORD_MARKER" not in row.state_json
        assert row.status == "error"
    assert calls == []


@pytest.mark.parametrize("bad", ["type", "credential", "unknown_successor"])
def test_plan_resume_revalidates_executable_arguments(runtime, bad):
    db, b = runtime
    calls = []
    state = AgentState(user_input="multi step", plan=plan_text())
    p = AgentPipeline(PlanLLM(), tools=registry(calls))
    p._prepare_structured_plan(state)
    if bad == "type":
        state.structured_plan["steps"][0]["arguments"]["value"] = "not an integer"
    elif bad == "credential":
        state.structured_plan["steps"][0]["arguments"]["password"] = "SYNTHETIC_PLAN_PASSWORD_MARKER"
    else:
        state.structured_plan["steps"][1]["tool"] = "not_registered"
    with Session(db) as session:
        store(session, b).save_checkpoint(**b, stage="plan", round_no=0, tool_budget_exhausted=False,
                                         state_json=StateManager(state).to_json(), status="running", error=None)
    with Session(db) as session:
        result = asyncio.run(AgentPipeline(PlanLLM(), tools=registry(calls),
            state_store=store(session, b), state_binding=b).run(AgentState(user_input="multi step")))
        assert result.error
    assert calls == []


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("stage", ["preflight", "quality"])
def test_plan_preflight_and_quality_runtime_timeout(monkeypatch, stream, stage):
    from app.agents.prompts import SYSTEM_QUALITY_GATE
    class SlowLLM(PlanLLM):
        async def chat(self, messages, options=None):
            target = SYSTEM_PREFLOW if stage == "preflight" else SYSTEM_QUALITY_GATE
            if messages[0].content == target:
                await asyncio.sleep(0.1)
            return await super().chat(messages, options)
    monkeypatch.setattr(settings, "AGENT_QUALITY_GATE_ENABLED", True)
    state = AgentState(user_input="multi step")
    p = AgentPipeline(SlowLLM(), tools=registry([]), options=LLMOptions(timeout=0.01))
    async def go():
        if stream:
            return [e.type async for e in p.run_stream(state)]
        await p.run(state)
    asyncio.run(go())
    assert state.error == "TimeoutError"


@pytest.mark.parametrize("stream", [False, True])
def test_plan_invalid_draft_has_safe_location(stream):
    class BadDraftLLM(PlanLLM):
        async def chat(self, messages, options=None):
            if messages[0].content == SYSTEM_ACT:
                return None
            return await super().chat(messages, options)
    state = AgentState(user_input="multi step")
    p = AgentPipeline(BadDraftLLM(), tools=registry([]))
    async def go():
        if stream:
            return [e.type async for e in p.run_stream(state)]
        await p.run(state)
    asyncio.run(go())
    assert state.error == "步骤 draft 失败（INVALID_STEP_OUTPUT）"


def test_plan_real_file_read_failure_stops_successor(monkeypatch, tmp_path):
    from app.agents.tools.file_ops import file_ops_tool

    monkeypatch.setattr(settings, "FILE_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "FILE_OPS_ALLOWED_ROOTS", json.dumps({"public": str(tmp_path)}))
    tool = file_ops_tool()
    assert tool is not None
    data = json.loads(plan_text())
    data["steps"][0]["tool"] = "file_ops"
    data["steps"][0]["arguments"] = {"root": "public", "path": "not-present.txt"}
    calls = []
    reg = registry(calls)
    reg.register(tool)
    state = asyncio.run(AgentPipeline(PlanLLM(json.dumps(data)), tools=reg).run(
        AgentState(user_input="multi step")))
    assert state.failed_step_id == "first"
    assert state.plan_progress[0]["status"] == "failed"
    assert state.plan_progress[1]["status"] == "pending"
    assert state.plan_cursor == 0
    assert calls == []
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("stream", [False, True])
def test_plan_resume_canonical_text_never_repersists_credentials(runtime, stream):
    from sqlmodel import select

    from app.models.agent_state import AgentRunState

    class RecordingLLM(PlanLLM):
        def __init__(self):
            super().__init__()
            self.seen = []
        async def chat(self, messages, options=None):
            self.seen.extend(m.content for m in messages)
            return await super().chat(messages, options)

    db, b = runtime
    state = AgentState(user_input="multi step", plan=plan_text())
    AgentPipeline(PlanLLM(), tools=registry([]))._prepare_structured_plan(state)
    state.plan = json.dumps({"password": "SYNTHETIC_TEXT_PASSWORD_MARKER"})
    with Session(db) as session:
        store(session, b).save_checkpoint(**b, stage="plan", round_no=0, tool_budget_exhausted=False,
            state_json=StateManager(state).to_json(), status="running", error=None)
    provider = RecordingLLM()
    calls = []
    with Session(db) as session:
        p = AgentPipeline(provider, tools=registry(calls), state_store=store(session, b), state_binding=b)
        result = AgentState(user_input="multi step")
        async def go():
            if stream:
                return [e.type async for e in p.run_stream(result)]
            await p.run(result)
        asyncio.run(go())
        assert not result.error
        assert calls == [1, 2]
        assert json.loads(result.plan) == result.structured_plan
    assert not any("SYNTHETIC_TEXT_PASSWORD_MARKER" in text for text in provider.seen)
    with Session(db) as session:
        row = session.exec(select(AgentRunState)).one()
        assert row.status == "done"
        assert "SYNTHETIC_TEXT_PASSWORD_MARKER" not in row.state_json
