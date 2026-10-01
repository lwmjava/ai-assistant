"""不需要工具和知识库的问题只生成一次。路由由代码决定，不另调模型。"""

import asyncio

import pytest

from app.agents.fast_path import iter_fast_path
from app.agents.pipeline import AgentState
from app.agents.route import RouteKind, route_message
from app.agents.tools.base import ToolRegistry
from app.agents.tools.builtin import default_tools
from app.core.config import settings
from app.llm.base import LLMOptions, LLMProvider
from app.services.chat_service import ChatService

_PLAIN = "一个agent应用平台系统需要具备哪些功能？以一个企业上线的项目作为衡量标准"


class _StreamOnce(LLMProvider):
    model = "stream-once"

    def __init__(self, text: str = "功能清单") -> None:
        self.text = text
        self.chat_calls = 0
        self.stream_calls = 0
        self.users: list[str] = []

    async def chat(self, messages, options=None) -> str:
        self.chat_calls += 1
        self.users.append(messages[-1].content)
        return self.text

    async def stream_chat(self, messages, options=None):
        self.stream_calls += 1
        self.users.append(messages[-1].content)
        yield self.text[:2]
        yield self.text[2:]


class _CalculatorLLM(LLMProvider):
    model = "calculator-script"

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    async def chat(self, messages, options=None) -> str:
        self.calls += 1
        self.prompts.append(messages[-1].content)
        if self.calls == 1:
            return '<tool_call>{"name": "calculator", "arguments": {"expression": "1+1"}}</tool_call>'
        return "等于 2"

    async def stream_chat(self, messages, options=None):
        yield await self.chat(messages, options)


class _Retriever:
    def __init__(self, text: str = "年假 5 天", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0
        self.last_hits: list = []

    async def retrieve(self, query: str, plan: str) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("检索不可用")
        return self.text


def _events(llm, state, route, retriever=None, tools=None):
    async def _run():
        found = []
        async for event in iter_fast_path(
            llm, LLMOptions(), state, route, retriever, tools
        ):
            found.append(event)
        return found

    return asyncio.run(_run())


def test_plain_enterprise_question_is_simple() -> None:
    route = route_message(_PLAIN)
    assert route.kind is RouteKind.SIMPLE
    assert route.tool_names == frozenset()


def test_explicit_marks_open_one_path() -> None:
    assert route_message("算一下 1+1").kind is RouteKind.TOOLS
    assert route_message("算一下 1+1").tool_names == frozenset({"calculator"})
    assert route_message("现在几点").tool_names == frozenset({"get_current_datetime"})
    assert route_message("运行这段代码").tool_names == frozenset({"code_sandbox"})
    assert route_message("根据知识库回答").kind is RouteKind.RAG
    assert route_message("查一下年假").kind is RouteKind.RAG
    assert route_message("先调研再写").kind is RouteKind.MULTI
    assert route_message("").kind is RouteKind.SIMPLE
    assert route_message("计算机有哪些部件").kind is RouteKind.SIMPLE


def test_tool_mark_wins_over_knowledge_and_multi() -> None:
    route = route_message("先调研知识库，再帮我算一下 1+1")
    assert route.kind is RouteKind.TOOLS
    assert route.tool_names == frozenset({"calculator"})


def test_knowledge_mark_wins_over_multi() -> None:
    assert route_message("先调研知识库里的制度").kind is RouteKind.RAG


def test_simple_path_streams_once_and_skips_tools_and_retrieval() -> None:
    llm = _StreamOnce("功能清单")
    retriever = _Retriever()
    state = AgentState(user_input=_PLAIN)
    events = _events(llm, state, route_message(_PLAIN), retriever, ToolRegistry(default_tools()))
    assert llm.stream_calls == 1
    assert llm.chat_calls == 0
    assert retriever.calls == 0
    assert state.answer == "功能清单"
    assert [event.data for event in events if event.type == "stage"] == ["响应"]
    assert "知识库" not in llm.users[0]
    assert "".join(event.data for event in events if event.type == "token") == "功能清单"


def test_tool_path_only_allows_named_tool_and_caps_rounds() -> None:
    llm = _CalculatorLLM()
    retriever = _Retriever()
    state = AgentState(user_input="算一下 1+1")
    events = _events(
        llm,
        state,
        route_message(state.user_input),
        retriever,
        ToolRegistry(default_tools()),
    )
    assert retriever.calls == 0
    assert llm.calls <= 2
    assert "calculator" in llm.prompts[0]
    assert "web_fetch" not in llm.prompts[0]
    assert any(event.type == "tool" and "calculator" in event.data for event in events)
    assert state.answer == "等于 2"


def test_rag_path_retrieves_once() -> None:
    llm = _StreamOnce("根据资料，年假 5 天")
    retriever = _Retriever()
    state = AgentState(user_input="根据知识库回答年假")
    _events(llm, state, route_message(state.user_input), retriever)
    assert retriever.calls == 1
    assert llm.stream_calls == 1
    assert llm.chat_calls == 0
    assert "年假 5 天" in llm.users[0]


def test_rag_without_retriever_or_on_failure_still_answers_once() -> None:
    llm = _StreamOnce("直接说明")
    state = AgentState(user_input="查一下年假")
    _events(llm, state, route_message(state.user_input), None)
    assert llm.stream_calls == 1
    assert "没有可用的检索结果" in llm.users[0]

    failed = _Retriever(fail=True)
    llm2 = _StreamOnce("改口回答")
    state2 = AgentState(user_input="查一下年假")
    _events(llm2, state2, route_message(state2.user_input), failed)
    assert failed.calls == 1
    assert llm2.stream_calls == 1
    assert "没有可用的检索结果" in llm2.users[0]


def test_service_fast_route_only_when_langgraph(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ChatService(_StreamOnce())
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self")
    assert service._fast_route("算一下 1+1") is None
    assert service._fast_route(_PLAIN) is None
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "langgraph")
    plain = service._fast_route(_PLAIN)
    assert plain is not None
    assert plain.kind is RouteKind.SIMPLE
    assert service._fast_route("先调研再写") is None
