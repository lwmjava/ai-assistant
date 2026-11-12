"""Measurement and optional model-tier routing stay off by default."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agents.pipeline import SYSTEM_UNDERSTAND, AgentPipeline, AgentState
from app.agents.route import RouteKind, route_message
from app.debug.trace import AgentTrace
from app.llm.model_tier import model_tier_routing_enabled, select_generation_task
from evals.skill_intent_overhead.compare import (
    EVAL_VERSION,
    case_result_from_trace,
    compare_reports,
    load_cases,
    score_route_and_selection,
    summarize_group,
)


class CountingModel:
    def __init__(self, name: str, reply: str = "YES"):
        self.model = name
        self.reply = reply
        self.calls = 0

    async def chat(self, messages, options=None):
        self.calls += 1
        await asyncio.sleep(0)
        return self.reply

    async def stream_chat(self, messages, options=None):
        self.calls += 1
        await asyncio.sleep(0)
        yield self.reply


def test_model_tier_switch_defaults_off_and_ignores_confidence():
    assert model_tier_routing_enabled() is False
    simple = route_message("你好")
    tools = route_message("请帮我算一下 2 加 2")
    assert simple.kind is RouteKind.SIMPLE
    assert tools.kind is RouteKind.TOOLS
    assert select_generation_task(simple) == "chat"
    assert select_generation_task(tools) == "chat"
    assert select_generation_task(None) == "chat"


def test_model_tier_uses_route_kind_not_confidence(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_MODEL_TIER_ROUTING_ENABLED", True)
    simple = route_message("你好")
    tools = route_message("请帮我算一下 2 加 2")
    rag = route_message("请根据知识库说明退货流程")
    combined = route_message("请根据报销标准计算差旅金额")
    assert select_generation_task(simple) == "intent"
    assert select_generation_task(tools) == "chat"
    assert select_generation_task(rag) == "chat"
    assert select_generation_task(combined) == "chat"
    # Confidence must not be consulted; the same simple route stays intent-tier.
    simple_with_low = SimpleNamespace(kind=simple.kind, tool_names=simple.tool_names, confidence=0.01)
    assert select_generation_task(simple_with_low) == "intent"


def test_trace_records_stage_purpose_and_eval_version_without_debug_events():
    trace = AgentTrace(eval_version=EVAL_VERSION)
    trace.start()
    with trace.measure("skill_match"):
        pass
    with trace.measure("route"):
        pass
    trace.stage_start("意图分流")
    trace.llm_call("intent-model", purpose="intent", stage="preflight", latency_ms=4)
    trace.stage_end("意图分流")
    trace.stage_start("检索")
    trace.stage_end("检索")
    trace.stage_start("行动")
    trace.tool_call("calculator", latency_ms=2)
    trace.stage_end("行动")
    trace.stage_start("响应")
    trace.mark_first_token(model=True)
    trace.llm_call("chat-model", purpose="chat", stage="respond", latency_ms=8)
    trace.stage_end("响应")
    trace.finish()
    summary = trace.summary()
    assert summary["eval_version"] == EVAL_VERSION
    assert summary["llm_call_count"] == 2
    assert summary["llm_calls_by_purpose"] == {"intent": 1, "chat": 1}
    assert summary["tool_call_count"] == 1
    for key in ("skill_match", "route", "preflight", "retrieval", "tools", "answer"):
        assert key in summary["stage_durations_ms"]
    assert summary["first_token_ms"] is not None
    assert summary["first_model_token_ms"] is not None
    assert trace.events == []


async def test_pipeline_counts_preflight_as_intent_purpose_without_debug_events():
    model = CountingModel("intent-model")
    trace = AgentTrace(eval_version=EVAL_VERSION)
    pipeline = AgentPipeline(CountingModel("chat-model"), intent_llm=model, trace=trace)
    state = AgentState(user_input="synthetic request")
    await pipeline._needs_plan(state)
    summary = trace.summary()
    assert summary["llm_call_count"] == 1
    assert summary["llm_calls_by_purpose"]["intent"] == 1
    assert "chat" not in summary["llm_calls_by_purpose"]
    assert trace.events == []


async def test_stream_marks_first_model_token():
    model = CountingModel("chat-model", "hello")
    trace = AgentTrace()
    pipeline = AgentPipeline(model, trace=trace, structured_plans_enabled=False)
    stream = pipeline._stream_final(AgentState(user_input="test"))
    pieces = [piece async for piece in stream]
    assert pieces == ["hello"]
    summary = trace.summary()
    assert summary["llm_call_count"] == 1
    assert summary["llm_calls_by_purpose"]["chat"] == 1
    assert summary["first_model_token_ms"] is not None
    assert trace.events == []


def test_chat_service_uses_intent_model_only_for_simple_routes_when_enabled(monkeypatch):
    from app.core.config import settings
    from app.services.chat_service import ChatService

    chat = CountingModel("chat-model")
    intent = CountingModel("intent-model")
    service = ChatService(llm_provider=chat, intent_llm_provider=intent)
    monkeypatch.setattr(settings, "LLM_MODEL_TIER_ROUTING_ENABLED", False)
    simple = route_message("你好")
    tools = route_message("请帮我算一下 3 加 5")
    assert service._generation_llm(simple) is chat
    assert service._generation_llm(tools) is chat
    monkeypatch.setattr(settings, "LLM_MODEL_TIER_ROUTING_ENABLED", True)
    assert service._generation_llm(simple) is intent
    assert service._generation_llm(tools) is chat
    assert service._intent_llm() is intent


def test_mock_overhead_comparison_is_not_an_adoption_decision():
    cases = load_cases()
    assert cases
    baseline_rows = []
    treatment_rows = []
    from app.core.config import settings
    original = settings.LLM_MODEL_TIER_ROUTING_ENABLED
    try:
        for enabled, rows in ((False, baseline_rows), (True, treatment_rows)):
            settings.LLM_MODEL_TIER_ROUTING_ENABLED = enabled
            for case in cases:
                scored = score_route_and_selection(case["query"], case)
                if enabled:
                    if scored["expected_route"] == "simple":
                        assert scored["generation_task"] == "intent"
                    else:
                        assert scored["generation_task"] == "chat"
                else:
                    assert scored["generation_task"] == "chat"
                trace = AgentTrace(eval_version=EVAL_VERSION)
                trace.start()
                with trace.measure("skill_match"):
                    pass
                with trace.measure("route"):
                    route_message(case["query"])
                trace.mark_first_token(model=True)
                trace.llm_call("mock", purpose="chat", stage="respond", latency_ms=1)
                with trace.measure("响应"):
                    pass
                trace.finish()
                rows.append(case_result_from_trace(case, scored, trace.summary()))
    finally:
        settings.LLM_MODEL_TIER_ROUTING_ENABLED = original

    baseline = summarize_group(baseline_rows, "baseline", "mock")
    treatment = summarize_group(treatment_rows, "model_tier", "mock")
    decision = compare_reports(baseline, treatment, source="mock")
    assert baseline["source"] == treatment["source"] == "mock"
    assert baseline["eval_version"] == EVAL_VERSION
    assert baseline["route_accuracy"] == 1.0
    assert treatment["route_accuracy"] == 1.0
    assert baseline["skill_accuracy"] == 1.0
    assert baseline["tool_accuracy"] == 1.0
    assert decision["decision"] == "not_a_real_baseline"
    assert decision["real_model_run"] is False


async def test_authority_check_still_blocks_before_model_call():
    model = CountingModel("chat-model")
    trace = AgentTrace()
    pipeline = AgentPipeline(model, trace=trace)

    def refuse():
        raise PermissionError("revoked")

    pipeline.authority_check = refuse
    with pytest.raises(PermissionError):
        await pipeline._stage(SYSTEM_UNDERSTAND, "_build_understand", AgentState(user_input="test"))
    assert model.calls == trace.summary()["llm_call_count"] == 0
