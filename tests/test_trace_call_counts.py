"""Model attempts remain observable without verbose trace payloads."""

import asyncio
from types import SimpleNamespace

import pytest

from app.agents.pipeline import SYSTEM_UNDERSTAND, AgentPipeline, AgentState
from app.agents.tools.base import ToolCall, ToolRegistry
from app.debug.trace import AgentTrace
from app.llm.base import ChatMessage, ChatRole, LLMOptions


class CountingModel:
    model = "counting-test"

    def __init__(self, outcome="ok"):
        self.calls = 0
        self.closed_streams = 0
        self.outcome = outcome

    async def chat(self, messages, options=None):
        self.calls += 1
        if self.outcome == "failed":
            raise RuntimeError("private failure text")
        if self.outcome == "cancelled":
            raise asyncio.CancelledError
        return "YES"

    async def stream_chat(self, messages, options=None):
        try:
            yield await self.chat(messages, options)
        finally:
            self.closed_streams += 1


@pytest.mark.parametrize("debug_mode", [False, True])
@pytest.mark.parametrize("operation", ["stage", "preflight", "quality"])
@pytest.mark.parametrize("outcome", ["ok", "failed", "cancelled"])
async def test_pipeline_counts_each_actual_attempt(debug_mode, operation, outcome):
    model = CountingModel(outcome)
    trace = AgentTrace(debug_mode=debug_mode)
    pipeline = AgentPipeline(model, trace=trace)
    state = AgentState(user_input="synthetic request")
    if operation == "stage":
        invoke = pipeline._stage(SYSTEM_UNDERSTAND, "_build_understand", state)
    elif operation == "preflight":
        invoke = pipeline._needs_plan(state)
    else:
        invoke = pipeline._evaluate_quality(state, "synthetic draft")
    if outcome == "ok":
        await invoke
    else:
        with pytest.raises(RuntimeError if outcome == "failed" else asyncio.CancelledError):
            await invoke
    assert model.calls == trace.summary()["llm_call_count"] == 1
    assert trace.summary()["total_llm_latency_ms"] >= 0
    assert len(trace.events) == (1 if debug_mode else 0)


async def test_refused_stage_does_not_count_an_unmade_request():
    model = CountingModel()
    trace = AgentTrace()
    pipeline = AgentPipeline(model, trace=trace)

    def refuse():
        raise PermissionError("revoked")

    pipeline.authority_check = refuse
    with pytest.raises(PermissionError):
        await pipeline._stage(SYSTEM_UNDERSTAND, "_build_understand", AgentState(user_input="test"))
    assert model.calls == trace.summary()["llm_call_count"] == 0


@pytest.mark.parametrize("outcome", ["ok", "failed", "cancelled"])
async def test_supervisor_counts_actual_model_attempt(outcome):
    from app.agents.supervisor import SupervisorGraph

    model = CountingModel(outcome)
    trace = AgentTrace()
    graph = SupervisorGraph(model, trace=trace)
    invoke = graph._chat([ChatMessage(role=ChatRole.USER, content="test")], LLMOptions())
    if outcome == "ok":
        await invoke
    else:
        with pytest.raises(RuntimeError if outcome == "failed" else asyncio.CancelledError):
            await invoke
    assert model.calls == trace.summary()["llm_call_count"] == 1
    assert trace.events == []


@pytest.mark.parametrize("outcome", ["ok", "failed", "cancelled", "closed"])
async def test_final_stream_counts_once_on_completion_or_close(outcome):
    model = CountingModel("ok" if outcome == "closed" else outcome)
    trace = AgentTrace()
    pipeline = AgentPipeline(model, trace=trace)
    stream = pipeline._stream_final(AgentState(user_input="test"))
    if outcome == "closed":
        await anext(stream)
        await stream.aclose()
    elif outcome == "ok":
        assert [piece async for piece in stream] == ["YES"]
    else:
        with pytest.raises(RuntimeError if outcome == "failed" else asyncio.CancelledError):
            async for _ in stream:
                pass
    assert model.calls == trace.summary()["llm_call_count"] == 1
    assert trace.events == []


@pytest.mark.parametrize("outcome", ["ok", "failed", "cancelled", "denied"])
async def test_executor_dispatch_counts_once_even_when_interrupted(outcome):
    trace = AgentTrace()
    calls = []

    async def execute(call):
        calls.append(call.name)
        if outcome == "failed":
            raise RuntimeError("synthetic error")
        if outcome == "cancelled":
            raise asyncio.CancelledError
        return SimpleNamespace(status=outcome, observation="safe result")

    pipeline = AgentPipeline(
        CountingModel(), trace=trace, tools=ToolRegistry([]), executor=SimpleNamespace(execute=execute),
    )
    invoke = pipeline._execute_tool(ToolCall("synthetic", {}), AgentState(user_input="test"))
    if outcome in {"failed", "cancelled"}:
        with pytest.raises(RuntimeError if outcome == "failed" else asyncio.CancelledError):
            await invoke
    else:
        await invoke
    assert calls == ["synthetic"]
    assert trace.summary()["tool_call_count"] == 1
    assert trace.events == []


def test_pipeline_tool_decisions_do_not_count_as_dispatches():
    trace = AgentTrace(debug_mode=True)
    pipeline = AgentPipeline(CountingModel(), trace=trace)
    for status in ("skipped", "invalid", "exhausted"):
        pipeline._record_tool_trace("tool", status, 1, 0)
    assert len(trace.events) == 3
    assert trace.summary()["tool_call_count"] == 0


async def test_public_pipeline_close_flushes_stream_count_immediately():
    model = CountingModel()
    trace = AgentTrace()
    pipeline = AgentPipeline(model, trace=trace, structured_plans_enabled=False)
    stream = pipeline.run_stream(AgentState(user_input="test"))
    async for event in stream:
        if event.type == "token":
            break
    else:
        pytest.fail("stream did not produce a token")
    await stream.aclose()
    assert model.calls == trace.summary()["llm_call_count"]
    assert model.calls > 0


@pytest.mark.parametrize("kind", ["simple", "rag"])
@pytest.mark.parametrize("outcome", ["ok", "failed", "cancelled", "closed"])
@pytest.mark.parametrize("debug_mode", [False, True])
async def test_fast_stream_counts_and_closes_actual_attempt(kind, outcome, debug_mode):
    from app.agents.fast_path import iter_fast_path
    from app.agents.route import ChatRoute, RouteKind

    model = CountingModel("ok" if outcome == "closed" else outcome)
    trace = AgentTrace(debug_mode=debug_mode)
    stream = iter_fast_path(
        model, LLMOptions(), AgentState(user_input="test"), ChatRoute(RouteKind(kind)), None, None, trace=trace,
    )

    async def consume():
        async for event in stream:
            if outcome == "closed" and event.type == "token":
                return

    try:
        if outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await consume()
        else:
            await consume()
    finally:
        await stream.aclose()
    assert model.calls == trace.summary()["llm_call_count"] == 1
    assert model.closed_streams == 1
    assert trace.summary()["tool_call_count"] == 0
    assert len(trace.events) == (1 if debug_mode else 0)


async def test_fast_disclosure_close_does_not_count_an_unmade_request():
    from app.agents.fast_path import iter_fast_path
    from app.agents.route import ChatRoute, RouteKind

    model = CountingModel()
    trace = AgentTrace()
    state = AgentState(user_input="test", retrieval_disclosure="synthetic disclosure")
    stream = iter_fast_path(model, LLMOptions(), state, ChatRoute(RouteKind.SIMPLE), None, None, trace=trace)
    async for event in stream:
        if event.type == "token":
            break
    await stream.aclose()
    assert model.calls == model.closed_streams == trace.summary()["llm_call_count"] == 0
