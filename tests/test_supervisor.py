"""Agent 编排增强测试：LangGraph Supervisor 边界 + Preflight 短路 + QualityGate 自纠错。

使用可控的脚本化 LLM（按系统提示词分支返回），避免依赖真实模型与网络。
"""

import asyncio
import json

import pytest

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.prompts import (
    SYSTEM_PREFLOW,
    SYSTEM_QUALITY_CRITIQUE,
    SYSTEM_QUALITY_GATE,
)
from app.agents.supervisor import SupervisorGraph
from app.agents.tools.base import ToolRegistry
from app.core.config import settings
from app.llm.base import LLMProvider
from app.llm.budget import ContextBudgetError
from app.llm.mock import MockLLMProvider
from app.services.chat_service import ChatService


class ScriptedLLM(LLMProvider):
    """按系统提示词分支返回固定内容的可控 LLM，用于驱动管线分支。"""

    model = "scripted"

    def __init__(
        self,
        *,
        preflight: str = "YES",
        quality_score: str = "0.9",
        critique: str = "修正要点",
        answer: str = "回答内容",
    ) -> None:
        self.preflight = preflight
        self.quality_score = quality_score
        self.critique = critique
        self.answer = answer

    async def chat(self, messages, options=None) -> str:
        system = messages[0].content if messages else ""
        if SYSTEM_PREFLOW in system:
            return self.preflight
        if SYSTEM_QUALITY_GATE in system:
            return self.quality_score
        if SYSTEM_QUALITY_CRITIQUE in system:
            return self.critique
        return self.answer

    async def stream_chat(self, messages, options=None):
        text = await self.chat(messages, options)
        yield text


# ── Supervisor 模块边界 ──────────────────────────────
def test_supervisor_module_importable_without_langgraph() -> None:
    from app.agents import supervisor  # noqa: F401

    assert hasattr(supervisor, "SupervisorGraph")


def test_supervisor_requires_langgraph_or_constructs() -> None:
    try:
        import langgraph  # noqa: F401

        graph = SupervisorGraph(MockLLMProvider())
        assert hasattr(graph, "run")
    except ImportError:
        with pytest.raises(ImportError):
            SupervisorGraph(MockLLMProvider())


# ── Preflight 意图短路 ────────────────────────────────
def test_preflight_short_circuit_skips_plan_and_reflect() -> None:
    llm = ScriptedLLM(preflight="NO", answer="直接回答")
    pipeline = AgentPipeline(llm)
    state = AgentState(user_input="你好")
    result = asyncio.run(pipeline.run(state))

    assert result.needs_full_pipeline is False
    assert result.plan == ""  # 规划环节被跳过
    assert result.reflection == ""  # 反思环节被跳过
    assert result.answer == "直接回答"


def test_preflight_full_pipeline_runs_when_yes() -> None:
    llm = ScriptedLLM(preflight="YES", answer="完整回答")
    pipeline = AgentPipeline(llm)
    state = AgentState(user_input="请解释 TCP 的工作原理")
    result = asyncio.run(pipeline.run(state))

    assert result.needs_full_pipeline is True
    assert result.plan != ""
    assert result.draft != ""
    assert result.reflection != ""
    assert result.answer == "完整回答"


# ── QualityGate 自纠错 ────────────────────────────────
def test_quality_gate_disabled_does_not_revise() -> None:
    saved = settings.AGENT_QUALITY_GATE_ENABLED
    settings.AGENT_QUALITY_GATE_ENABLED = False
    try:
        llm = ScriptedLLM(preflight="YES", answer="草稿", quality_score="0.1")
        pipeline = AgentPipeline(llm)
        state = AgentState(user_input="一个问题")
        result = asyncio.run(pipeline.run(state))
        assert result.revision == 0
        assert result.quality_score == 0.0  # 关闭时未评分
    finally:
        settings.AGENT_QUALITY_GATE_ENABLED = saved


def test_quality_gate_revises_when_below_threshold() -> None:
    saved_enabled = settings.AGENT_QUALITY_GATE_ENABLED
    saved_threshold = settings.AGENT_QUALITY_THRESHOLD
    saved_revisions = settings.AGENT_MAX_REVISIONS
    settings.AGENT_QUALITY_GATE_ENABLED = True
    settings.AGENT_QUALITY_THRESHOLD = 0.7
    settings.AGENT_MAX_REVISIONS = 2
    try:
        llm = ScriptedLLM(
            preflight="YES",
            answer="草稿",
            quality_score="0.5",
            critique="补充论据",
        )
        pipeline = AgentPipeline(llm)
        state = AgentState(user_input="一个问题")
        result = asyncio.run(pipeline.run(state))
        assert result.quality_score == 0.5
        assert result.revision >= 1  # 至少触发一次自纠错
    finally:
        settings.AGENT_QUALITY_GATE_ENABLED = saved_enabled
        settings.AGENT_QUALITY_THRESHOLD = saved_threshold
        settings.AGENT_MAX_REVISIONS = saved_revisions


def test_quality_gate_no_revision_when_above_threshold() -> None:
    saved_enabled = settings.AGENT_QUALITY_GATE_ENABLED
    saved_threshold = settings.AGENT_QUALITY_THRESHOLD
    settings.AGENT_QUALITY_GATE_ENABLED = True
    settings.AGENT_QUALITY_THRESHOLD = 0.7
    try:
        llm = ScriptedLLM(
            preflight="YES",
            answer="草稿",
            quality_score="0.9",
            critique="修正要点",
        )
        pipeline = AgentPipeline(llm)
        state = AgentState(user_input="一个问题")
        result = asyncio.run(pipeline.run(state))
        assert result.quality_score == 0.9
        assert result.revision == 0  # 达标直接通过
    finally:
        settings.AGENT_QUALITY_GATE_ENABLED = saved_enabled
        settings.AGENT_QUALITY_THRESHOLD = saved_threshold


class _SupervisorScript(LLMProvider):
    """按系统提示词区分调度、调研和撰写。调度动作按调用顺序给出。"""

    model = "supervisor-script"

    def __init__(
        self,
        actions: list[str],
        research: list[str],
        draft: str,
    ) -> None:
        self.actions = actions
        self.research = research
        self.draft = draft
        self._action_index = 0
        self._research_index = 0

    async def chat(self, messages, options=None) -> str:
        system = messages[0].content if messages else ""
        if "编排调度器" in system:
            action = self.actions[self._action_index]
            self._action_index += 1
            return action
        if "撰写者" in system:
            return self.draft
        text = self.research[self._research_index]
        self._research_index += 1
        return text

    async def stream_chat(self, messages, options=None):
        text = await self.chat(messages, options)
        yield text


class _BoomLLM(LLMProvider):
    """图执行一开始就失败，异常原文里带一段密钥样式。"""

    model = "boom"

    async def chat(self, messages, options=None) -> str:
        raise RuntimeError("sk-" + "c" * 24)

    async def stream_chat(self, messages, options=None):
        if False:
            yield ""


async def _collect(graph: SupervisorGraph, state: AgentState) -> list:
    events = []
    async for event in graph.run_stream(state):
        events.append(event)
    return events


def _subtask_payloads(events: list) -> list[dict]:
    return [json.loads(event.data) for event in events if event.type == "subtask"]


def test_supervisor_delegates_and_returns_summary() -> None:
    llm = _SupervisorScript(
        actions=["research", "draft", "FINISH"],
        research=["调研记录"],
        draft="汇总结果",
    )
    graph = SupervisorGraph(llm, max_revisions=2)
    state = AgentState(user_input="请核对这条事实")
    events = asyncio.run(_collect(graph, state))

    assert [(item["name"], item["result"]) for item in state.delegations] == [
        ("research", "调研记录"),
        ("draft", "汇总结果"),
    ]
    assert state.answer == "汇总结果"

    payloads = _subtask_payloads(events)
    assert [item["summary"] for item in payloads] == ["调研记录", "汇总结果"]
    assert all(item["v"] == 1 and item["status"] == "done" for item in payloads)
    assert [item["name"] for item in payloads] == ["research", "draft"]
    types = [event.type for event in events]
    assert types.index("subtask") < types.index("token")


def test_supervisor_keeps_both_research_rounds() -> None:
    llm = _SupervisorScript(
        actions=["research", "research", "draft", "FINISH"],
        research=["调研记录甲", "调研记录乙"],
        draft="汇总结果",
    )
    graph = SupervisorGraph(llm, max_revisions=2)
    state = AgentState(user_input="连续调研")
    events = asyncio.run(_collect(graph, state))

    assert [item["name"] for item in state.delegations] == [
        "research",
        "research",
        "draft",
    ]
    assert [item["result"] for item in state.delegations] == [
        "调研记录甲",
        "调研记录乙",
        "汇总结果",
    ]

    payloads = _subtask_payloads(events)
    assert len(payloads) == 3
    assert payloads[0]["name"] == "research"
    assert payloads[0]["summary"] == "调研记录甲"
    assert payloads[1]["summary"] == "调研记录乙"
    assert payloads[2]["name"] == "draft"
    assert payloads[2]["summary"] == "汇总结果"


def test_supervisor_summary_is_redacted_and_truncated() -> None:
    secret = "sk-" + "b" * 24
    tail = "不应出现在摘要末尾的标记"
    research = f"发现 {secret} 。" + ("资" * 520) + tail
    llm = _SupervisorScript(
        actions=["research", "draft", "FINISH"],
        research=[research],
        draft="汇总结果",
    )
    graph = SupervisorGraph(llm, max_revisions=2)
    state = AgentState(user_input="含密钥的调研")
    events = asyncio.run(_collect(graph, state))
    assert state.delegations[0]["result"] == research
    assert secret in state.delegations[0]["result"]
    assert tail in state.delegations[0]["result"]

    payloads = _subtask_payloads(events)
    summary = payloads[0]["summary"]
    assert secret not in summary
    assert tail not in summary
    assert len(summary) <= 500
    assert "***" in summary
    joined = "".join(event.data for event in events if event.type == "subtask")
    assert secret not in joined
    assert tail not in joined


def test_supervisor_empty_result_uses_fixed_summary() -> None:
    llm = _SupervisorScript(
        actions=["research", "draft", "FINISH"],
        research=[""],
        draft="汇总结果",
    )
    graph = SupervisorGraph(llm, max_revisions=2)
    state = AgentState(user_input="调研没有文本")
    events = asyncio.run(_collect(graph, state))
    payloads = _subtask_payloads(events)
    assert state.delegations[0] == {"name": "research", "result": ""}
    assert payloads[0]["status"] == "done"
    assert payloads[0]["summary"] == "没有文本结果"
    assert payloads[1]["summary"] == "汇总结果"


def test_supervisor_error_skips_subtask_and_hides_exception() -> None:
    graph = SupervisorGraph(_BoomLLM())
    events = asyncio.run(_collect(graph, AgentState(user_input="会失败")))
    blob = "".join(event.data for event in events)
    assert all(event.type != "subtask" for event in events)
    assert "sk-" not in blob
    assert "抱歉，多 Agent 协作处理时出现问题，请稍后重试。" in blob


def test_supervisor_budget_failure_tells_user_to_shorten_not_retry() -> None:
    """编排路径的预算超限必须给可照做的提示。

    说「请稍后重试」会诱导用户重试一次必然再被拦的请求，所以这里既要断言
    出现「请缩短输入」，也要断言**不出现**「请稍后重试」。
    """

    class _BudgetLLM(LLMProvider):
        model = "budget"

        async def chat(self, messages, options=None) -> str:
            raise ContextBudgetError(
                "output_reserve_exceeded",
                payload_tokens=999999,
                reserved_output=2048,
                margin=512,
                limit=131072,
            )

        async def stream_chat(self, messages, options=None):
            if False:
                yield ""

    graph = SupervisorGraph(_BudgetLLM())
    events = asyncio.run(_collect(graph, AgentState(user_input="会超预算")))
    blob = "".join(event.data for event in events)

    assert "请缩短输入" in blob
    assert "请稍后重试" not in blob
    assert "999999" not in blob


def test_supervisor_drafts_once_when_model_keeps_asking_to_draft() -> None:
    class _Repeat(LLMProvider):
        def __init__(self) -> None:
            self.drafts = 0
            self.systems: list[str] = []

        async def chat(self, messages, options=None) -> str:
            system = messages[0].content if messages else ""
            self.systems.append(system)
            if "编排调度器" in system:
                return "draft"
            if "撰写者" in system:
                self.drafts += 1
                return f"第{self.drafts}次"
            return "不该调研"

        async def stream_chat(self, messages, options=None):
            yield await self.chat(messages, options)

    llm = _Repeat()
    state = AgentState(user_input="写一段说明")
    asyncio.run(_collect(SupervisorGraph(llm), state))
    assert llm.drafts == 1
    assert state.answer == "第1次"
    assert state.delegations == [{"name": "draft", "result": "第1次"}]
    assert all("执行器" not in system for system in llm.systems)


def test_supervisor_research_is_one_call_and_stops_at_two() -> None:
    llm = _SupervisorScript(
        actions=["research", "research", "research"],
        research=["甲", "乙", "丙"],
        draft="收束",
    )
    state = AgentState(user_input="先写调研")
    asyncio.run(_collect(SupervisorGraph(llm), state))
    assert [item["name"] for item in state.delegations] == ["research", "research", "draft"]
    assert [item["result"] for item in state.delegations] == ["甲", "乙", "收束"]
    assert state.answer == "收束"
    assert llm._research_index == 2


def test_default_orchestration_stays_on_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self")
    built = ChatService(MockLLMProvider())._build_pipeline(None, ToolRegistry([]))
    assert type(built) is AgentPipeline


def test_langgraph_import_error_falls_back_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "langgraph")

    def _missing():
        raise ImportError("langgraph 未安装")

    monkeypatch.setattr(SupervisorGraph, "_require_langgraph", staticmethod(_missing))
    built = ChatService(MockLLMProvider())._build_pipeline(None, ToolRegistry([]))
    assert type(built) is AgentPipeline
