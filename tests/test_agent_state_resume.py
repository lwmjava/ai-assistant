"""PAGT-006 修复轮：生产绑定（真实 message_id）多轮隔离 / 续跑 / 失败路径反例。

等价真实入口：真实 AgentStateService + 隔离 SQLite + 真实 AgentPipeline 动作循环，
非纯单元 Mock。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlmodel import Session, delete, select

from app.agents.pipeline import AgentPipeline, AgentState, ToolRegistry, tool_fingerprint
from app.agents.state import StateManager
from app.agents.tools.base import Tool, ToolCall
from app.agents.tools.builtin import default_tools
from app.core.database import engine
from app.models.agent_state import AgentRunState
from app.models.conversation import Conversation, Message
from app.models.user import Tenant, User
from app.services.agent_state_service import AgentStateService


class ScriptLLM:
    model = "script-resume"

    def __init__(self, reply) -> None:
        self.reply = reply

    async def chat(self, messages, options=None) -> str:
        return self.reply(messages[0].content, messages[-1].content)

    async def stream_chat(self, messages, options=None):
        yield "好的"


def _echo_tool():
    calls = {"n": 0}

    async def echo(arguments):
        calls["n"] += 1
        return f"echo:{arguments.get('q', '')}"

    return Tool(name="echo", description="", parameters={"type": "object"}, func=echo), calls


@pytest.fixture()
def conv_pair():
    with Session(engine) as session:
        tenant = Tenant(name="pagt006fix-tenant")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id, username="pagt006fix-user", hashed_password="x", role="member"
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        conv = Conversation(tenant_id=tenant.id, user_id=user.id, title="fix")
        session.add(conv)
        session.commit()
        session.refresh(conv)
        m1 = Message(conversation_id=conv.id, role="user", content="第一轮 echo")
        m2 = Message(conversation_id=conv.id, role="user", content="第二轮 echo")
        session.add(m1)
        session.add(m2)
        session.commit()
        session.refresh(m1)
        session.refresh(m2)
        session.exec(delete(AgentRunState))
        session.commit()
        ids = {
            "tenant_id": tenant.id, "user_id": user.id, "conv_id": conv.id,
            "m1": m1.id, "m2": m2.id,
        }
        yield ids
        session.exec(delete(AgentRunState))
        session.exec(delete(Message).where(Message.conversation_id == conv.id))
        session.exec(delete(Conversation).where(Conversation.id == conv.id))
        session.exec(delete(User).where(User.id == user.id))
        session.exec(delete(Tenant).where(Tenant.id == tenant.id))
        session.commit()


# ── P1-1：真实 message_id 绑定下，跨消息不污染 ──────────────────────


def test_multi_chat_messages_isolated_no_state_leak(conv_pair) -> None:
    # 第一轮 m1 完成：写一条 done checkpoint（含 echo 指纹与观测）
    echo_fp = tool_fingerprint("echo", {"q": "hi"})
    with Session(engine) as session:
        svc = AgentStateService(session, tenant_id=conv_pair["tenant_id"], user_id=conv_pair["user_id"])
        mid = AgentState(user_input="第一轮")
        mid.executed_tool_fingerprints = {echo_fp}
        mid.tool_results.append('[echo] 参数=... 结果=echo:hi')
        svc.save_checkpoint(
            conversation_id=conv_pair["conv_id"], message_id=conv_pair["m1"],
            tenant_id=conv_pair["tenant_id"], user_id=conv_pair["user_id"],
            stage="done", round_no=1, tool_budget_exhausted=False,
            state_json=StateManager(mid).to_json(), status="done", error=None,
        )
    # 第二轮 m2：全新 pipeline、绑真实 m2 id；起始 _resume_state 不得继承 m1
    tool, calls = _echo_tool()
    registry = ToolRegistry([tool])
    llm = ScriptLLM(lambda s, u: '<tool_call>{"name": "echo", "arguments": {"q": "hi"}}</tool_call>')
    pipeline = AgentPipeline(llm, tools=registry)
    with Session(engine) as session:
        svc2 = AgentStateService(session, tenant_id=conv_pair["tenant_id"], user_id=conv_pair["user_id"])
        pipeline.state_store = svc2
        pipeline.state_binding = {
            "conversation_id": conv_pair["conv_id"], "message_id": conv_pair["m2"],
            "tenant_id": conv_pair["tenant_id"], "user_id": conv_pair["user_id"],
        }
        fresh = AgentState(user_input="第二轮")
        pipeline._resume_state(fresh)
        assert fresh.executed_tool_fingerprints == set(), "第二轮不得继承第一轮 echo 指纹"
        assert fresh.tool_results == [], "第二轮不得继承第一轮 tool_results"
        # m2 的合法 echo 调用应真实执行，而非被残留指纹 skip
        asyncio.run(pipeline._run_action_loop(fresh))
        assert calls["n"] == 1, "第二轮 echo 应真实执行一次"


def test_resume_same_message_continues_not_restarting(conv_pair) -> None:
    """同一条用户消息 m1 中断（running）后续跑：已完成 echo 不重跑。"""
    echo_fp = tool_fingerprint("echo", {"q": "hi"})
    with Session(engine) as session:
        svc = AgentStateService(session, tenant_id=conv_pair["tenant_id"], user_id=conv_pair["user_id"])
        mid = AgentState(user_input="m1")
        mid.executed_tool_fingerprints = {echo_fp}
        mid.tool_results.append('[echo] r')
        svc.save_checkpoint(
            conversation_id=conv_pair["conv_id"], message_id=conv_pair["m1"],
            tenant_id=conv_pair["tenant_id"], user_id=conv_pair["user_id"],
            stage="action", round_no=1, tool_budget_exhausted=False,
            state_json=StateManager(mid).to_json(), status="running", error=None,
        )
    tool, calls = _echo_tool()
    registry = ToolRegistry([tool])
    llm = ScriptLLM(lambda s, u: '<tool_call>{"name": "echo", "arguments": {"q": "hi"}}</tool_call>')
    pipeline = AgentPipeline(llm, tools=registry)
    with Session(engine) as session:
        svc2 = AgentStateService(session, tenant_id=conv_pair["tenant_id"], user_id=conv_pair["user_id"])
        pipeline.state_store = svc2
        pipeline.state_binding = {
            "conversation_id": conv_pair["conv_id"], "message_id": conv_pair["m1"],
            "tenant_id": conv_pair["tenant_id"], "user_id": conv_pair["user_id"],
        }
        resumed = AgentState(user_input="m1")
        pipeline._resume_state(resumed)
        assert echo_fp in resumed.executed_tool_fingerprints  # 指纹保留
        asyncio.run(pipeline._run_action_loop(resumed))
        assert calls["n"] == 0  # 续跑：已完成工具不重复执行


# ── P2-1：失败路径 / 事件契约 / 并发 / 4 工具 golden ─────────────────


def test_checkpoint_persist_failure_does_not_break_main_chain(conv_pair, monkeypatch) -> None:
    class FlakyStore:
        def save_checkpoint(self, **kwargs):
            raise RuntimeError("db down")

    tool, calls = _echo_tool()
    registry = ToolRegistry([tool])
    llm = ScriptLLM(lambda s, u: '<tool_call>{"name": "echo", "arguments": {"q": "hi"}}</tool_call>')
    pipeline = AgentPipeline(llm, tools=registry)
    pipeline.state_store = FlakyStore()
    pipeline.state_binding = {"conversation_id": conv_pair["conv_id"], "message_id": conv_pair["m1"]}
    fresh = AgentState(user_input="x")
    # checkpoint 写失败不得阻断主链；echo 仍执行、回复照常产出
    asyncio.run(pipeline._run_action_loop(fresh))
    assert calls["n"] == 1


def test_agent_event_contract_preserves_legacy_payloads_and_typed_boundaries() -> None:
    legacy = {"stage", "token", "tool", "code_result", "done", "error"}
    boundaries = {"node.started", "node.finished", "intent.classified"}
    llm = ScriptLLM(lambda s, u: "直接回答")
    pipeline = AgentPipeline(llm)
    seen = []

    async def collect():
        async for ev in pipeline.run_stream(AgentState(user_input="你好")):
            seen.append(ev)

    asyncio.run(collect())
    kinds = {event.type for event in seen}
    assert kinds <= legacy | boundaries
    assert {"stage", "done"} <= kinds
    assert boundaries <= kinds
    started, finished = [], []
    for event in seen:
        assert isinstance(event.data, str)
        assert event.to_dict() == {"type": event.type, "data": event.data}
        if event.type.startswith("node."):
            payload = json.loads(event.data)
            assert isinstance(payload["node"], str) and payload["node"]
            assert isinstance(payload["ts"], int | float)
            if event.type == "node.started":
                started.append(payload["node"])
            else:
                assert payload["status"] in {"done", "failed", "cancelled"}
                finished.append(payload["node"])
        elif event.type == "intent.classified":
            payload = json.loads(event.data)
            assert payload["intent"] in {"full", "direct"}
            assert isinstance(payload["shortcut"], bool)
    assert started == finished


def test_concurrent_conflict_does_not_corrupt_other_rows(conv_pair) -> None:
    with Session(engine) as session:
        svc = AgentStateService(session, tenant_id=conv_pair["tenant_id"], user_id=conv_pair["user_id"])
        # 同绑定两次 save → upsert 单行
        for i in range(2):
            svc.save_checkpoint(
                conversation_id=conv_pair["conv_id"], message_id=conv_pair["m1"],
                tenant_id=conv_pair["tenant_id"], user_id=conv_pair["user_id"],
                stage="action", round_no=i + 1, tool_budget_exhausted=False,
                state_json='{"plan":"m1"}', status="running", error=None,
            )
        # 异绑定另一行
        svc.save_checkpoint(
            conversation_id=conv_pair["conv_id"], message_id=conv_pair["m2"],
            tenant_id=conv_pair["tenant_id"], user_id=conv_pair["user_id"],
            stage="action", round_no=1, tool_budget_exhausted=False,
            state_json='{"plan":"m2"}', status="running", error=None,
        )
        rows = session.exec(select(AgentRunState)).all()
        by_msg = {r.message_id: r for r in rows}
        assert by_msg[conv_pair["m1"]].round_no == 2  # upsert 取最新
        assert by_msg[conv_pair["m2"]].state_json == '{"plan":"m2"}'  # 他行未被覆盖


def test_all_builtin_tools_bound_legacy_observation_golden(monkeypatch) -> None:
    from datetime import datetime
    from types import SimpleNamespace

    from app.agents.tools import builtin

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 13, 12, 0, 0)

    # Equality compares transport behavior, so both real calls use the same clock.
    monkeypatch.setattr(builtin, "datetime", SimpleNamespace(datetime=FixedDatetime))
    reg = ToolRegistry(default_tools())
    cases = [
        ToolCall(name="calculator", arguments={"expression": "1+1"}),
        ToolCall(name="calculator", arguments={}),
        ToolCall(name="get_current_datetime", arguments={}),
        ToolCall(name="code_sandbox", arguments={}),
    ]
    for call in cases:
        legacy = asyncio.run(reg.run(call))
        bound = asyncio.run(__import__(
            "app.agents.tools.executor", fromlist=["ToolExecutor"]
        ).ToolExecutor(reg).execute(call))
        assert bound.observation == legacy, f"{call.name} bound/legacy 不一致"
