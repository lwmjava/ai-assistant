"""工具调用（Function Calling）测试。"""

import asyncio

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.tools.base import Tool, ToolRegistry, parse_tool_call
from app.agents.tools.builtin import default_tools
from app.llm.mock import MockLLMProvider


def test_parse_tool_call_valid() -> None:
    text = '无关文本 <tool_call>{"name": "calc", "arguments": {"x": 1}}</tool_call> 后缀'
    call = parse_tool_call(text)
    assert call is not None
    assert call.name == "calc"
    assert call.arguments == {"x": 1}


def test_parse_tool_call_invalid() -> None:
    assert parse_tool_call("普通回答文本") is None
    assert parse_tool_call("<tool_call>{bad json}</tool_call>") is None
    assert parse_tool_call('<tool_call>{"arguments": {}}</tool_call>') is None


def test_registry_register_and_describe() -> None:
    reg = ToolRegistry()
    assert reg.get("missing") is None
    reg.register(Tool(name="t", description="d", parameters={}, func=lambda a: "ok"))
    assert reg.get("t") is not None
    assert "t" in reg.describe()


def test_calculator_tool() -> None:
    reg = ToolRegistry(default_tools())
    tool = reg.get("calculator")
    result = asyncio.run(tool.execute({"expression": "6 * 7 + 1"}))
    assert result == "43"


def test_calculator_rejects_dangerous_input() -> None:
    reg = ToolRegistry(default_tools())
    tool = reg.get("calculator")
    result = asyncio.run(tool.execute({"expression": "__import__('os')"}))
    assert "失败" in result


def test_datetime_tool() -> None:
    reg = ToolRegistry(default_tools())
    tool = reg.get("get_current_datetime")
    result = asyncio.run(tool.execute({}))
    assert len(result) >= 8  # 至少包含日期部分


def test_pipeline_invokes_tool_and_continues() -> None:
    """自定义 LLM 首次行动返回工具调用，第二次返回最终答案。"""

    class ToolCallingLLM:
        model = "fake"

        async def chat(self, messages, options=None):
            content = messages[-1].content
            if "## 可用外部工具" in content and not getattr(self, "_used", False):
                self._used = True
                return '<tool_call>{"name": "calculator", "arguments": {"expression": "6*7"}}</tool_call>'
            return "最终答案：已计算。"

        async def stream_chat(self, messages, options=None):
            yield "最终答案：已计算。"

    pipeline = AgentPipeline(ToolCallingLLM(), tools=ToolRegistry(default_tools()))
    state = AgentState(user_input="帮我算 6*7")
    result = asyncio.run(pipeline.run(state))

    assert len(result.tool_results) == 1
    assert "42" in result.tool_results[0]
    assert result.answer
    assert result.error is None


def test_pipeline_stream_emits_tool_event() -> None:
    """流式模式下，工具调用应通过 tool 事件广播。"""

    class ToolCallingLLM:
        model = "fake"
        _used = False

        async def chat(self, messages, options=None):
            content = messages[-1].content
            if "## 可用外部工具" in content and not self._used:
                self._used = True
                return '<tool_call>{"name": "calculator", "arguments": {"expression": "2+3"}}</tool_call>'
            return "完成。"

        async def stream_chat(self, messages, options=None):
            yield "完成。"

    pipeline = AgentPipeline(ToolCallingLLM(), tools=ToolRegistry(default_tools()))
    state = AgentState(user_input="算一下")

    async def _collect():
        return [event.type async for event in pipeline.run_stream(state)]

    types = asyncio.run(_collect())
    assert "tool" in types
    assert types[-1] == "done"


def test_pipeline_without_tools_skips_action_loop() -> None:
    """无工具时，「行动」阶段直接产出草稿，不进入工具循环。"""
    pipeline = AgentPipeline(MockLLMProvider())
    state = AgentState(user_input="你好")
    result = asyncio.run(pipeline.run(state))
    assert result.tool_results == []
    assert result.answer
