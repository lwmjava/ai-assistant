"""Agent 五阶段管线测试（使用 Mock 提供商）。"""

import asyncio

from app.agents.pipeline import AgentPipeline, AgentState
from app.llm.mock import MockLLMProvider


def test_pipeline_run_populates_all_stages() -> None:
    pipeline = AgentPipeline(MockLLMProvider())
    state = AgentState(user_input="什么是 RAG？")
    result = asyncio.run(pipeline.run(state))

    assert result.answer
    assert result.understanding
    assert result.plan
    assert result.draft
    assert result.reflection
    assert result.error is None


def test_pipeline_run_stream_emits_events() -> None:
    pipeline = AgentPipeline(MockLLMProvider())
    state = AgentState(user_input="你好")
    types = asyncio.run(_collect_types(pipeline, state))

    assert "stage" in types
    assert "token" in types
    assert types[-1] == "done"


async def _collect_types(pipeline: AgentPipeline, state: AgentState) -> list[str]:
    return [event.type async for event in pipeline.run_stream(state)]
