"""SSE protocol assertions at real AgentPipeline entry points (synthetic models)."""

import asyncio
import json

import pytest

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.plan_runtime import PlanExecutionError
from app.agents.prompts import SYSTEM_ACT, SYSTEM_PLAN, SYSTEM_PREFLOW
from app.agents.tools.base import Tool, ToolRegistry
from app.agents.tools.executor import ToolExecutor
from app.core.config import settings
from app.llm.budget import ContextBudgetError
from app.llm.mock import MockLLMProvider
from app.llm.routing import LLMUnavailableError


class Script(MockLLMProvider):
    async def chat(self, messages, options=None):
        system = messages[0].content
        if system == SYSTEM_PREFLOW:
            return "YES"
        if system == SYSTEM_PLAN:
            return json.dumps({"schema_version": 1, "steps": [{
                "id": "check", "description": "check", "kind": "tool", "tool": "probe",
                "arguments": {"value": "private-input"}, "depends_on": [], "expected_output": "checked",
            }]})
        if system == SYSTEM_ACT:
            return "checked"
        return "answer"


class QuietRegistry(ToolRegistry):
    async def _record_audit(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_pipeline_events_have_real_node_boundaries_and_intent_plan(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_QUALITY_GATE_ENABLED", False)
    pipeline = AgentPipeline(MockLLMProvider())
    events = []
    original = pipeline._stage

    async def stage(system, builder_name, state):
        started = [json.loads(event.data) for event in events if event.type == "node.started"]
        assert started, "node.started must be delivered before stage model execution"
        return await original(system, builder_name, state)

    monkeypatch.setattr(pipeline, "_stage", stage)
    async for event in pipeline.run_stream(AgentState(user_input="explain workflow")):
        events.append(event)
    types = [event.type for event in events]
    assert "node.started" in types
    assert "node.finished" in types
    assert "intent.classified" in types
    assert isinstance(json.loads(next(event.data for event in events if event.type == "plan.generated"))["steps"], list)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_structured_tool_call_precedes_execution_and_result_reports_real_outcome(monkeypatch, fail):
    monkeypatch.setattr(settings, "AGENT_QUALITY_GATE_ENABLED", False)
    events = []
    calls = []

    async def probe(args):
        calls.append(args)
        assert any(event.type == "tool.call" for event in events), "tool.call must precede side effect"
        if fail:
            raise ConnectionError("private-provider-detail")
        return "private-result"

    registry = QuietRegistry([Tool("probe", "synthetic", {"type": "object"}, probe)])
    pipeline = AgentPipeline(Script(), tools=registry, executor=ToolExecutor(registry))
    async for event in pipeline.run_stream(AgentState(user_input="perform check")):
        events.append(event)
    assert len(calls) == 1
    results = [json.loads(event.data) for event in events if event.type == "tool.result"]
    assert len(results) == 1
    assert results[0]["ok"] is (not fail)
    call = next(json.loads(event.data) for event in events if event.type == "tool.call")
    assert call["call_id"] == results[0]["call_id"]
    public = " ".join(event.data for event in events if event.type in {"tool.call", "tool.result"})
    assert "private-input" not in public
    assert "private-result" not in public
    assert "private-provider-detail" not in public


@pytest.mark.asyncio
async def test_successful_quality_gate_has_real_node_boundary(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_QUALITY_GATE_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_MAX_REVISIONS", 1)
    pipeline = AgentPipeline(MockLLMProvider())
    events = []

    async def score(state, draft):
        assert json.loads([e.data for e in events if e.type == "node.started"][-1])["node"] == "quality_gate"
        return 1.0

    monkeypatch.setattr(pipeline, "_evaluate_quality", score)
    async for event in pipeline.run_stream(AgentState(user_input="explain workflow")):
        events.append(event)
    finished = [json.loads(e.data) for e in events if e.type == "node.finished"]
    assert any(e["node"] == "quality_gate" and e["status"] == "done" for e in finished)


@pytest.mark.asyncio
async def test_stage_failure_finishes_actual_node_as_failed(monkeypatch):
    pipeline = AgentPipeline(MockLLMProvider())

    async def stage(*args):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(pipeline, "_stage", stage)
    events = [event async for event in pipeline.run_stream(AgentState(user_input="explain workflow"))]
    finished = [json.loads(e.data) for e in events if e.type == "node.finished"]
    assert [(e["node"], e["status"]) for e in finished] == [("understanding", "failed")]


@pytest.mark.asyncio
async def test_cancel_finishes_started_node_without_success(monkeypatch):
    pipeline = AgentPipeline(MockLLMProvider())
    entered = asyncio.Event()
    events = []

    async def stage(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(pipeline, "_stage", stage)

    async def consume():
        async for event in pipeline.run_stream(AgentState(user_input="explain workflow")):
            events.append(event)

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    finished = [json.loads(e.data) for e in events if e.type == "node.finished"]
    assert [(e["node"], e["status"]) for e in finished] == [("understanding", "cancelled")]


@pytest.mark.asyncio
@pytest.mark.parametrize("exc,code,step", [
    (PlanExecutionError("TOOL_FAILED", "step2"), "TOOL_FAILED", "step2"),
    (PlanExecutionError("TOOL_FAILED", "private/value"), "TOOL_FAILED", "plan"),
    (PlanExecutionError("private/value", "step2"), "generation_failed", None),
    (RuntimeError("private/value"), "generation_failed", None),
    (LLMUnavailableError(), "model_unavailable", None),
    (ContextBudgetError("capability_unverified", payload_tokens=1, reserved_output=1, margin=1, limit=1),
     "context_budget_exceeded", None),
])
async def test_error_preserves_legacy_string_and_supplies_controlled_metadata(monkeypatch, exc, code, step):
    pipeline = AgentPipeline(MockLLMProvider())

    async def stage(*args):
        raise exc

    monkeypatch.setattr(pipeline, "_stage", stage)
    events = [event async for event in pipeline.run_stream(AgentState(user_input="explain workflow"))]
    errors = [event for event in events if event.type == "error"]
    assert len(errors) == 1
    event = errors[0]
    assert isinstance(event.data, str)
    assert set(event.to_dict()) == {"type", "data"}
    assert event.metadata["code"] == code
    assert event.metadata.get("step_id") == step
    assert isinstance(event.metadata["retryable"], bool)
    assert "private/value" not in event.data + json.dumps(event.metadata)
    if step == "step2":
        assert "step2" in event.data and "TOOL_FAILED" in event.data
