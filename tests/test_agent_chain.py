"""意图分流、历史截取和历史脱敏。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.agents.pipeline import (
    AgentPipeline,
    AgentState,
    ExecutionPolicy,
    build_preflight_user_content,
    preflight_needs_full_pipeline,
    recent_dialogue,
)
from app.agents.tools.base import Tool, ToolRegistry
from app.agents.tools.builtin import publish_code_result
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.debug.trace import AgentTrace
from app.llm.base import ChatMessage, ChatRole, LLMProvider
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.services.chat_service import HISTORY_REDACTED, ChatService, sanitize_history_text

PHONE = "13800138000"
TOKEN = "sk-" + "a" * 24
SECRET_FIELD = "token=synthetic-secret-value"
SENTINELS = (PHONE, TOKEN, "synthetic-secret-value")


class CaptureLLM(LLMProvider):
    def __init__(self, reply: str = "YES") -> None:
        self.model = "capture"
        self.reply = reply
        self.seen: list[str] = []

    async def chat(self, messages, options=None) -> str:
        self.seen.append("\n".join(message.content for message in messages))
        return self.reply

    async def stream_chat(self, messages, options=None):
        yield await self.chat(messages, options)


def _message(role: str, content: str) -> ChatMessage:
    return ChatMessage(role=ChatRole(role), content=content)


def test_recent_dialogue_keeps_options_at_the_end_of_a_long_reply() -> None:
    options = "\n1. 执行代码\n2. 取消"
    content = "开头标记HEAD" + ("甲" * 3200) + options
    text = recent_dialogue([_message("assistant", content)])
    assert "开头标记HEAD" in text
    assert "1. 执行代码" in text
    assert "2. 取消" in text


def test_recent_dialogue_protects_latest_turn_inside_budget() -> None:
    user = "U-HEAD-" + ("a" * 1880) + "-U-TAIL"
    assistant = "A-HEAD-" + ("b" * 1880) + "-A-TAIL"
    older = "DROP-ME" + ("x" * 280)
    history = [_message("user", older), _message("user", user), _message("assistant", assistant)]
    text = recent_dialogue(history)
    assert "DROP-ME" not in text
    assert "U-HEAD-" in text and "-U-TAIL" in text
    assert "A-HEAD-" in text and "-A-TAIL" in text
    assert text.count("a") + text.count("b") <= 4000


def test_recent_dialogue_without_history() -> None:
    assert recent_dialogue([]) == "（无历史对话）"


@pytest.mark.parametrize(
    ("raw", "full"),
    [
        ("NO", False),
        ("NO。", False),
        (" no ", False),
        ("YES", True),
        ("NOT SURE", True),
        ("NONE", True),
        ("I don't know", True),
        ("", True),
        (None, True),
    ],
)
def test_preflight_decision_reads_only_the_first_word(raw: str | None, full: bool) -> None:
    assert preflight_needs_full_pipeline(raw) is full


def test_preflight_request_contains_previous_options() -> None:
    history = [
        _message("user", "请写冒泡排序"),
        _message("assistant", "请选择：\n1. 执行代码\n2. 只解释"),
    ]
    text = build_preflight_user_content(history, "1")
    assert "1. 执行代码" in text
    assert "## 用户最新消息" in text
    assert text.strip().endswith("1")


def test_pipeline_preflight_uses_recent_dialogue_and_strict_decision() -> None:
    intent = CaptureLLM("NOT SURE")
    pipeline = AgentPipeline(CaptureLLM("草稿"), intent_llm=intent)
    history = [_message("assistant", "请选择：\n1. 执行代码\n2. 只解释")]
    result = asyncio.run(pipeline._needs_plan(AgentState(user_input="1", history=history)))
    assert result is True
    assert "1. 执行代码" in intent.seen[0]

    intent.reply = "NO。"
    result = asyncio.run(pipeline._needs_plan(AgentState(user_input="你好")))
    assert result is False


def test_sanitize_history_text_removes_sentinels_without_security_flags() -> None:
    from app.core.config import settings

    original = settings.SECURITY_ENABLED
    settings.SECURITY_ENABLED = False
    try:
        text = sanitize_history_text(f"联系 {PHONE}，{SECRET_FIELD}，密钥 {TOKEN}")
    finally:
        settings.SECURITY_ENABLED = original
    for sentinel in SENTINELS:
        assert sentinel not in text


def test_sanitize_history_text_does_not_use_request_security(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def deny(self, *args, **kwargs):
        called.append("called")
        raise AssertionError("历史脱敏不应调用请求级安全检查")

    monkeypatch.setattr("app.security.rate_limiter.RateLimiter.allow", deny)
    monkeypatch.setattr("app.security.prompt_injection.PromptInjectionDetector.detect", deny)
    assert PHONE not in sanitize_history_text(f"号码 {PHONE}")
    assert called == []


def test_sanitize_history_text_failure_hides_original(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self, text: str, ctx=None):
        raise RuntimeError("filter failed")

    monkeypatch.setattr("app.security.input_filter.InputFilter.filter", broken)
    assert sanitize_history_text(f"号码 {PHONE}") == HISTORY_REDACTED


def _conversation() -> Conversation:
    start = datetime(2026, 9, 27, tzinfo=UTC)
    conv = Conversation(id="conv-1", tenant_id="tenant-a", user_id="user-a", title="合成")
    conv.messages = []
    for index in range(31):
        secret = f" {PHONE} {SECRET_FIELD} {TOKEN}" if index in {0, 30} else ""
        conv.messages.append(
            Message(
                conversation_id=conv.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"第{index}条 OLD-FACT-ALPHA{secret}" if index == 0 else f"第{index}条普通内容{secret}",
                created_at=start + timedelta(seconds=index),
                updated_at=start + timedelta(seconds=index),
            )
        )
    return conv


def test_memory_prompts_and_reflection_hide_history_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "MEMORY_ENABLED", True)
    service = __import__("app.services.chat_service", fromlist=["ChatService"]).ChatService(CaptureLLM("摘要"))
    conv = _conversation()
    memory = asyncio.run(service._build_memory(conv))
    visible = "\n".join(service.llm.seen)
    visible += "\n" + "\n".join(message.content for message in memory.recent_messages)
    visible += "\n" + (memory.memory_context or "")
    pipeline = AgentPipeline(service.llm, intent_llm=service.llm)
    asyncio.run(
        pipeline.run(
            AgentState(
                user_input="1",
                history=memory.recent_messages,
                context=memory.memory_context,
            )
        )
    )
    visible += "\n" + "\n".join(service.llm.seen)
    reflection = service._build_reflect_conversation_text(conv)
    visible += "\n" + reflection
    assert "OLD-FACT-ALPHA" in visible
    for sentinel in SENTINELS:
        assert sentinel not in visible


def test_memory_fallback_also_sanitizes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(self, messages, snapshot=None):
        raise RuntimeError("memory failed")

    monkeypatch.setattr("app.services.chat_service.MemoryManager.manage", broken)
    service = __import__("app.services.chat_service", fromlist=["ChatService"]).ChatService(CaptureLLM())
    memory = asyncio.run(service._build_memory(_conversation()))
    visible = "\n".join(message.content for message in memory.recent_messages)
    assert "第30条普通内容" in visible
    for sentinel in SENTINELS:
        assert sentinel not in visible


HOST_PATH = r"C:\Users\secret\note.txt"
_SAME_CALL = '<tool_call>{"name": "echo", "arguments": {"q": "same"}}</tool_call>'


class ScriptLLM(LLMProvider):
    def __init__(self, reply) -> None:
        self.model = "script"
        self.reply = reply
        self.prompts: list[str] = []
        self.calls = 0

    async def chat(self, messages, options=None) -> str:
        self.calls += 1
        user = messages[-1].content
        system = messages[0].content
        self.prompts.append(user)
        if callable(self.reply):
            return self.reply(system, user)
        return self.reply

    async def stream_chat(self, messages, options=None):
        yield "最终回答"


def _echo_registry() -> tuple[ToolRegistry, dict[str, int]]:
    counter = {"n": 0}

    def echo(arguments: dict) -> str:
        counter["n"] += 1
        return f"echo:{arguments.get('q', '')}"

    denied = Tool(name="refund_tool", description="退款", parameters={}, func=lambda arguments: "no")
    tool = Tool(name="echo", description="回显", parameters={"type": "object"}, func=echo)
    return ToolRegistry([tool, denied]), counter


def _pipeline(reply, rounds: int = 5) -> tuple[AgentPipeline, ScriptLLM, dict[str, int]]:
    llm = ScriptLLM(reply)
    registry, counter = _echo_registry()
    pipeline = AgentPipeline(llm, tools=registry)
    pipeline.max_tool_rounds = rounds
    return pipeline, llm, counter


def test_duplicate_tool_call_executes_once_and_wraps_up() -> None:
    def reply(system: str, user: str) -> str:
        if "可用外部工具" in user:
            return _SAME_CALL
        return "收尾草稿"

    pipeline, llm, counter = _pipeline(reply, rounds=5)

    async def go() -> str:
        return await pipeline._run_action_loop(AgentState(user_input="算一下"))

    draft = asyncio.run(go())
    assert counter["n"] == 1
    assert llm.calls == 3
    assert "<tool_call>" not in draft
    assert "工具次数已用完" in llm.prompts[-1]


def test_distinct_arguments_stop_at_round_limit() -> None:
    seen = {"n": 0}

    def reply(system: str, user: str) -> str:
        if "可用外部工具" not in user:
            return _SAME_CALL
        seen["n"] += 1
        return f'<tool_call>{{"name": "echo", "arguments": {{"q": "{seen["n"]}"}}}}</tool_call>'

    pipeline, _llm, counter = _pipeline(reply, rounds=3)
    draft = asyncio.run(pipeline._run_action_loop(AgentState(user_input="继续")))
    assert counter["n"] == 3
    assert draft == "工具调用次数已用完，未能得到最终结果。"
    assert "<tool_call>" not in draft


@pytest.mark.parametrize(
    "bad",
    [
        "<tool_call>{bad json}</tool_call>",
        '<tool_call>{"name": 1, "arguments": {}}</tool_call>',
        '<tool_call>{"name": ["echo"], "arguments": {}}</tool_call>',
        '<tool_call>{"name": "echo", "arguments": [1]}</tool_call>',
        '<tool_call>{"name": "echo", "arguments": {}}</tool_call',
        _SAME_CALL + _SAME_CALL,
    ],
)
def test_malformed_tool_call_is_not_executed(bad: str) -> None:
    def reply(system: str, user: str) -> str:
        if "可用外部工具" in user:
            return bad
        return "干净草稿"

    pipeline, _llm, counter = _pipeline(reply, rounds=1)
    draft = asyncio.run(pipeline._run_action_loop(AgentState(user_input="坏格式")))
    assert counter["n"] == 0
    assert draft == "干净草稿"
    assert "<tool_call>" not in draft


def test_zero_tool_rounds_still_returns_draft() -> None:
    pipeline, llm, counter = _pipeline(lambda system, user: "非空草稿", rounds=0)
    draft = asyncio.run(pipeline._run_action_loop(AgentState(user_input="不要工具")))
    assert counter["n"] == 0
    assert draft == "非空草稿"
    assert "可用外部工具" not in llm.prompts[0]
    assert "工具次数已用完" in llm.prompts[0]


def test_single_tool_call_does_not_add_wrap_up() -> None:
    def reply(system: str, user: str) -> str:
        if "可用外部工具" in user and "已调用工具结果" not in user:
            return _SAME_CALL
        return "草稿完成"

    pipeline, llm, counter = _pipeline(reply, rounds=5)
    draft = asyncio.run(pipeline._run_action_loop(AgentState(user_input="一次")))
    assert counter["n"] == 1
    assert llm.calls == 2
    assert draft == "草稿完成"


def test_execution_policy_filters_prompt_and_execution() -> None:
    def reply(system: str, user: str) -> str:
        if "可用外部工具" in user:
            return '<tool_call>{"name": "refund_tool", "arguments": {}}</tool_call>'
        return "不允许执行"

    pipeline, llm, counter = _pipeline(reply, rounds=1)
    policy = ExecutionPolicy(allowed_tool_names=frozenset({"echo"}))

    async def go() -> str:
        return await pipeline._run_action_loop(AgentState(user_input="退款"), policy)

    draft = asyncio.run(go())
    assert counter["n"] == 0
    assert "echo" in llm.prompts[0]
    assert "refund_tool" not in llm.prompts[0]
    assert "<tool_call>" not in draft


def test_run_action_loop_returns_text_for_supervisor() -> None:
    pipeline, _llm, counter = _pipeline(lambda system, user: "直接回答", rounds=2)
    result = asyncio.run(pipeline._run_action_loop(AgentState(user_input="调研")))
    assert isinstance(result, str)
    assert result == "直接回答"
    assert counter["n"] == 0


def test_quality_gate_does_not_reexecute_same_call() -> None:
    saved_enabled = settings.AGENT_QUALITY_GATE_ENABLED
    saved_revisions = settings.AGENT_MAX_REVISIONS
    settings.AGENT_QUALITY_GATE_ENABLED = True
    settings.AGENT_MAX_REVISIONS = 1

    def reply(system: str, user: str) -> str:
        if "质量评估员" in system:
            return "0.10"
        if "修正导师" in system:
            return "补齐"
        if "可用外部工具" in user:
            return _SAME_CALL
        return "收尾草稿"

    pipeline, _llm, counter = _pipeline(reply, rounds=5)

    async def go() -> None:
        state = AgentState(user_input="重复")
        await pipeline._run_action_loop(state)
        await pipeline._quality_gate_loop(state)

    try:
        asyncio.run(go())
    finally:
        settings.AGENT_QUALITY_GATE_ENABLED = saved_enabled
        settings.AGENT_MAX_REVISIONS = saved_revisions
    assert counter["n"] == 1


def test_stream_quality_gate_emits_tool_event() -> None:
    saved_enabled = settings.AGENT_QUALITY_GATE_ENABLED
    saved_revisions = settings.AGENT_MAX_REVISIONS
    settings.AGENT_QUALITY_GATE_ENABLED = True
    settings.AGENT_MAX_REVISIONS = 1
    phase = {"scored": False, "critiqued": False, "revision_tool": False, "first": False}

    def reply(system: str, user: str) -> str:
        if "意图分析器" in system:
            return "意图"
        if "意图分流器" in system:
            return "YES"
        if "任务规划器" in system:
            return "计划"
        if "质量评估员" in system:
            phase["scored"] = True
            return "0.10" if not phase["critiqued"] else "0.90"
        if "修正导师" in system:
            phase["critiqued"] = True
            return "补齐"
        if "批判审查员" in system:
            return "无需修正"
        if "最终应答者" in system:
            return "最终回答"
        if "可用外部工具" not in user:
            return "收尾草稿"
        if phase["critiqued"] and not phase["revision_tool"]:
            phase["revision_tool"] = True
            return '<tool_call>{"name": "echo", "arguments": {"q": "second"}}</tool_call>'
        if not phase["first"]:
            phase["first"] = True
            return '<tool_call>{"name": "echo", "arguments": {"q": "first"}}</tool_call>'
        return "草稿完成"

    pipeline, _llm, counter = _pipeline(reply, rounds=5)

    async def go() -> list[str]:
        state = AgentState(user_input="流式质量门")
        return [event.type async for event in pipeline.run_stream(state)]

    try:
        types = asyncio.run(go())
    finally:
        settings.AGENT_QUALITY_GATE_ENABLED = saved_enabled
        settings.AGENT_MAX_REVISIONS = saved_revisions
    assert types.count("tool") == 2
    assert counter["n"] == 2
    assert types[-1] == "done"


def test_trace_records_tool_statuses_without_secrets() -> None:
    def reply(system: str, user: str) -> str:
        if "意图分析器" in system:
            return "意图"
        if "意图分流器" in system:
            return "YES"
        if "任务规划器" in system:
            return "计划"
        if "批判审查员" in system:
            return "无需修正"
        if "最终应答者" in system:
            return "最终回答"
        if "可用外部工具" in user:
            return _SAME_CALL
        return "收尾草稿"

    llm = ScriptLLM(reply)
    registry, counter = _echo_registry()
    trace = AgentTrace(debug_mode=True)
    pipeline = AgentPipeline(llm, tools=registry, trace=trace)
    pipeline.max_tool_rounds = 5
    user = f"请处理 {PHONE} {TOKEN} {SECRET_FIELD} {HOST_PATH}"
    asyncio.run(pipeline.run(AgentState(user_input=user)))
    trace.finish(error=f"失败 {PHONE} {TOKEN} {HOST_PATH}")
    dumped = str(trace.to_dict())
    statuses = [event.data.get("status") for event in trace.events if event.type == "tool_call"]
    assert counter["n"] == 1
    assert "executed" in statuses
    assert "skipped" in statuses
    assert "exhausted" in statuses
    for event in trace.events:
        if event.type != "tool_call":
            continue
        assert "args" not in event.data
        assert "result" not in event.data
        assert "result_preview" not in event.data
    for sentinel in (*SENTINELS, HOST_PATH, PHONE, TOKEN):
        assert sentinel not in dumped


_OPTIONS = "请选择：\n1. 写入沙箱并执行\n2. 只解释算法"
_SANDBOX_CALL = '<tool_call>{"name": "code_sandbox", "arguments": {"code": "print(1)"}}</tool_call>'


def _followup_history(code: str = "print('demo')") -> list[ChatMessage]:
    return [
        ChatMessage(role=ChatRole.USER, content=code),
        ChatMessage(role=ChatRole.ASSISTANT, content=_OPTIONS),
    ]


def _mixed_registry() -> tuple[ToolRegistry, dict[str, int]]:
    counter = {"calculator": 0, "code_sandbox": 0, "web_fetch": 0, "mcp": 0, "unknown": 0}

    def make(name: str):
        def run(arguments: dict) -> str:
            counter[name] += 1
            if name == "code_sandbox":
                publish_code_result({"status": "ok", "stdout": "1", "reason": ""})
            return name

        return run

    tools = [
        Tool(name="calculator", description="计算", parameters={}, func=make("calculator")),
        Tool(name="code_sandbox", description="沙箱", parameters={}, func=make("code_sandbox")),
        Tool(name="get_current_datetime", description="时间", parameters={}, func=lambda arguments: "now"),
        Tool(name="web_fetch", description="网页", parameters={}, func=make("web_fetch")),
        Tool(name="mcp__crm__get_order", description="订单", parameters={}, func=make("mcp")),
        Tool(name="refund_tool", description="退款", parameters={}, func=make("unknown")),
    ]
    return ToolRegistry(tools), counter


class _RouteLLM(LLMProvider):
    def __init__(self, preflight: str, act: str = "直接草稿") -> None:
        self.model = "route"
        self.preflight = preflight
        self.act = act
        self.calls = 0
        self.prompts: list[str] = []

    async def chat(self, messages, options=None) -> str:
        self.calls += 1
        system = messages[0].content
        user = messages[-1].content
        self.prompts.append(user)
        if "意图分流器" in system:
            return self.preflight
        if "可用外部工具" in user:
            return self.act
        return "最终回答"

    async def stream_chat(self, messages, options=None):
        yield "最终回答"


def test_act_and_respond_keep_long_code_in_recent_dialogue() -> None:
    code = "x" * 1500
    pipeline, _llm, _counter = _pipeline(lambda system, user: "草稿")
    state = AgentState(user_input="1", history=_followup_history(code), context="记忆摘要")
    act = pipeline._build_act(state)
    respond = pipeline._build_respond(state)
    assert code in act
    assert code in respond
    assert "最近对话" in act
    assert state.context == "记忆摘要"


def test_short_path_without_tool_keeps_call_count() -> None:
    pipeline, llm, counter = _pipeline(None)
    llm = _RouteLLM("NO", act="直接草稿")
    pipeline.llm = llm
    pipeline.intent_llm = llm
    result = asyncio.run(pipeline.run(AgentState(user_input="你好")))
    assert result.needs_full_pipeline is False
    assert result.plan == ""
    assert result.reflection == ""
    assert counter["n"] == 0
    assert llm.calls == 4


@pytest.mark.parametrize("preflight", ["YES", "NO"])
def test_followup_executes_sandbox_on_full_and_short_paths(preflight: str) -> None:
    registry, counter = _mixed_registry()
    llm = _RouteLLM(preflight, act=_SANDBOX_CALL)
    pipeline = AgentPipeline(llm, tools=registry)
    state = AgentState(user_input="1", history=_followup_history())
    result = asyncio.run(pipeline.run(state))
    assert counter["code_sandbox"] == 1
    assert counter["web_fetch"] == 0
    assert result.code_results[0]["stdout"] == "1"
    assert "<tool_call>" not in result.answer
    if preflight == "NO":
        assert result.plan == ""
        assert result.reflection == ""
        assert "web_fetch" not in llm.prompts[2]
        assert "mcp__crm__get_order" not in llm.prompts[2]
        assert "code_sandbox" in llm.prompts[2]


def test_short_path_rejects_tools_outside_whitelist() -> None:
    registry, counter = _mixed_registry()
    llm = _RouteLLM("NO", act='<tool_call>{"name": "web_fetch", "arguments": {"url": "https://example.test"}}</tool_call>')
    pipeline = AgentPipeline(llm, tools=registry)
    pipeline.max_tool_rounds = 1
    result = asyncio.run(pipeline.run(AgentState(user_input="你好")))
    assert counter["web_fetch"] == 0
    assert "web_fetch" not in llm.prompts[2]
    assert "<tool_call>" not in result.answer

    llm.act = '<tool_call>{"name": "mcp__crm__get_order", "arguments": {}}</tool_call>'
    llm.calls = 0
    llm.prompts.clear()
    result = asyncio.run(pipeline.run(AgentState(user_input="你好")))
    assert counter["mcp"] == 0
    assert "mcp__" not in llm.prompts[2]

    llm.act = '<tool_call>{"name": "refund_tool", "arguments": {}}</tool_call>'
    llm.prompts.clear()
    asyncio.run(pipeline.run(AgentState(user_input="你好")))
    assert counter["unknown"] == 0


def test_short_path_stream_emits_tool_for_safe_tool() -> None:
    registry, counter = _mixed_registry()
    llm = _RouteLLM("NO", act='<tool_call>{"name": "calculator", "arguments": {"expression": "1+1"}}</tool_call>')
    pipeline = AgentPipeline(llm, tools=registry)

    async def go() -> list[str]:
        state = AgentState(user_input="算一下")
        return [event.type async for event in pipeline.run_stream(state)]

    types = asyncio.run(go())
    assert "规划" not in types
    assert "tool" in types
    assert counter["calculator"] == 1
    assert types[-1] == "done"


def test_chat_service_persists_code_results_for_both_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RAG_ENABLED", False)
    monkeypatch.setattr(settings, "MCP_ENABLED", False)

    async def fake_sandbox(arguments: dict) -> str:
        publish_code_result({"status": "ok", "stdout": "persisted", "reason": ""})
        return "persisted"

    monkeypatch.setattr("app.agents.tools.builtin.code_sandbox", fake_sandbox)

    class PersistLLM(LLMProvider):
        def __init__(self, preflight: str) -> None:
            self.model = "persist"
            self.preflight = preflight
            self.used = False

        async def chat(self, messages, options=None) -> str:
            system = messages[0].content
            user = messages[-1].content
            if "意图分流器" in system:
                return self.preflight
            if "可用外部工具" in user and not self.used:
                self.used = True
                return _SANDBOX_CALL
            return "已执行选项 1"

        async def stream_chat(self, messages, options=None):
            yield "已执行选项 1"

    async def once(preflight: str) -> str:
        user = User(
            id=f"agt003-{preflight}",
            tenant_id="agt003-tenant",
            username=f"agt003-{preflight}",
            hashed_password="",
            role=Role.MEMBER.value,
            token_version=0,
            is_active=True,
        )
        service = ChatService(PersistLLM(preflight))
        with Session(engine) as session:
            conv = Conversation(tenant_id=user.tenant_id, user_id=user.id, title="选项")
            session.add(conv)
            session.commit()
            session.add(Message(conversation_id=conv.id, role="user", content="请用 code_sandbox 执行冒泡排序"))
            session.add(Message(conversation_id=conv.id, role="assistant", content=_OPTIONS))
            session.commit()
            await service.chat(session, user, "1", conv.id)
            stored = session.exec(
                select(Message).where(Message.conversation_id == conv.id, Message.role == "assistant")
            ).all()
            coded = [item for item in stored if item.code_results]
            assert coded
            payload = json.loads(coded[-1].code_results or "[]")
            assert payload[0]["stdout"] == "persisted"
            return conv.id

    asyncio.run(once("YES"))
    asyncio.run(once("NO"))
