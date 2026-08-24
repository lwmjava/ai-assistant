"""Agent 编排增强测试：LangGraph Supervisor 边界 + Preflight 短路 + QualityGate 自纠错。

使用可控的脚本化 LLM（按系统提示词分支返回），避免依赖真实模型与网络。
"""

import asyncio

import pytest

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.prompts import (
    SYSTEM_PREFLOW,
    SYSTEM_QUALITY_CRITIQUE,
    SYSTEM_QUALITY_GATE,
)
from app.agents.supervisor import SupervisorGraph
from app.core.config import settings
from app.llm.base import LLMProvider
from app.llm.mock import MockLLMProvider


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
