"""Empty action output must fail through the real Pipeline entry points."""
import asyncio
import json

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.plan_runtime import PlanExecutionError
from app.agents.prompts import SYSTEM_ACT, SYSTEM_PREFLOW, SYSTEM_RESPOND
from app.agents.tools.base import Tool, ToolRegistry
from app.agents.tools.builtin import publish_code_result
from app.core.config import settings
from app.llm.base import ChatMessage, ChatRole, LLMProvider
from app.models.agent_state import AgentRunState
from app.models.conversation import Conversation, Message
from app.models.user import Tenant, User
from app.services.agent_state_service import AgentStateService

CALL = '<tool_call>{"name":"code_sandbox","arguments":{"code":"print(1)"}}</tool_call>'


class Replies(LLMProvider):
    model = "isolated-empty-action-regression"

    def __init__(self, action_replies, route="NO"):
        self.actions = iter(action_replies)
        self.route = route
        self.calls = 0
        self.final_calls = 0

    async def chat(self, messages, options=None):
        self.calls += 1
        system = messages[0].content
        if system == SYSTEM_PREFLOW:
            return self.route
        if system.startswith(SYSTEM_ACT):
            return next(self.actions)
        if system == SYSTEM_RESPOND:
            self.final_calls += 1
            return "最终回复"
        return "理解或计划"

    async def stream_chat(self, messages, options=None):
        self.final_calls += 1
        yield "最终回复"


def state():
    return AgentState(user_input="1", history=[
        ChatMessage(role=ChatRole.USER, content="请执行代码 print(1)"),
        ChatMessage(role=ChatRole.ASSISTANT, content="1. 执行代码；2. 取消"),
    ])


def execute(pipeline, current, stream):
    async def run():
        if stream:
            return [e async for e in pipeline.run_stream(current)]
        await pipeline.run(current)
        return []
    return asyncio.run(run())


@pytest.mark.parametrize("empty", ["", " \r\n\t"], ids=["empty", "whitespace"])
@pytest.mark.parametrize("stream", [False, True], ids=["run", "stream"])
@pytest.mark.parametrize("route", ["NO", "YES"], ids=["short-default", "full-text-compat"])
def test_empty_action_fails_before_final_response(empty, stream, route, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_QUALITY_GATE_ENABLED", False)
    llm = Replies([empty], route)
    pipeline = AgentPipeline(llm, structured_plans_enabled=route == "NO")
    checkpoints = []
    monkeypatch.setattr(pipeline, "_checkpoint", lambda current, **kw: checkpoints.append(kw))
    current = state()
    events = execute(pipeline, current, stream)
    assert current.error == "EMPTY_ACTION_OUTPUT"
    assert current.answer == "抱歉，处理你的请求时出现问题，请稍后重试。"
    assert current.code_results == []
    assert llm.final_calls == 0
    assert checkpoints[-1]["status"] == "error"
    if stream:
        assert [e.type for e in events][-2:] == ["error", "done"]
        assert not any(e.type in {"token", "tool", "code_result"} for e in events)


@pytest.mark.parametrize("stream", [False, True], ids=["run", "stream"])
def test_empty_output_after_tool_preserves_completed_result(stream):
    called = []

    async def recording(arguments):
        called.append(arguments)
        publish_code_result({"status": "ok", "stdout": "1", "reason": ""})
        return "1"

    registry = ToolRegistry([Tool("code_sandbox", "recording", {}, recording)])
    llm = Replies([CALL, ""])
    pipeline = AgentPipeline(llm, tools=registry)
    current = state()
    execute(pipeline, current, stream)
    assert current.error == "EMPTY_ACTION_OUTPUT"
    assert len(called) == len(current.executed_tool_fingerprints) == 1
    assert current.code_results[0]["stdout"] == "1"
    assert current.tool_results
    assert llm.final_calls == 0


def test_empty_budget_wrapup_fails_without_extra_retry():
    llm = Replies([""])
    pipeline = AgentPipeline(llm)
    pipeline.max_tool_rounds = 0
    current = state()
    execute(pipeline, current, False)
    assert current.error == "EMPTY_ACTION_OUTPUT"
    assert llm.calls == 3
    assert current.tool_budget_exhausted


def test_valid_no_tool_short_reply_keeps_four_model_calls():
    llm = Replies(["正常无工具草稿"])
    current = state()
    execute(AgentPipeline(llm), current, False)
    assert current.error is None
    assert current.answer == "最终回复"
    assert llm.calls == 4


def test_structured_plan_empty_output_keeps_existing_error_code():
    pipeline = AgentPipeline(Replies([""]))
    current = state()
    current.structured_plan = {"schema_version": 1, "steps": []}
    with pytest.raises(PlanExecutionError) as error:
        asyncio.run(pipeline._stage(SYSTEM_ACT, "_build_act", current))
    assert error.value.code == "INVALID_STEP_OUTPUT"


def test_active_skill_short_path_still_rejects_empty_action():
    llm = Replies([""])
    pipeline = AgentPipeline(llm)
    pipeline.skill_prompt_injection = "合成技能说明"
    current = state()
    execute(pipeline, current, False)
    assert current.error == "EMPTY_ACTION_OUTPUT"
    assert llm.final_calls == 0


def test_active_skill_structured_action_keeps_step_error():
    pipeline = AgentPipeline(Replies([""]))
    pipeline.skill_prompt_injection = "合成技能说明"
    current = state()
    current.structured_plan = {"schema_version": 1, "steps": []}
    with pytest.raises(PlanExecutionError) as error:
        asyncio.run(pipeline._stage(SYSTEM_ACT, "_build_act", current))
    assert error.value.code == "INVALID_STEP_OUTPUT"


@pytest.mark.parametrize("stream", [False, True], ids=["run", "stream"])
def test_real_checkpoint_reread_is_error_not_done(stream, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_STATE_PERSIST_ENABLED", True)
    database = create_engine("sqlite://")
    SQLModel.metadata.create_all(database)
    with Session(database) as session:
        tenant = Tenant(name="empty-action-test")
        session.add(tenant)
        session.commit()
        user = User(tenant_id=tenant.id, username="empty-action-test", hashed_password="synthetic")
        session.add(user)
        session.commit()
        conversation = Conversation(tenant_id=tenant.id, user_id=user.id, title="empty")
        session.add(conversation)
        session.commit()
        message = Message(conversation_id=conversation.id, role="user", content="1")
        session.add(message)
        session.commit()
        binding = dict(conversation_id=conversation.id, message_id=message.id,
                       tenant_id=tenant.id, user_id=user.id)
        store = AgentStateService(session, tenant_id=tenant.id, user_id=user.id)
        pipeline = AgentPipeline(Replies([""]), state_store=store, state_binding=binding)
        execute(pipeline, state(), stream)
    with Session(database) as fresh:
        row = fresh.exec(select(AgentRunState)).one()
        assert row.status == "error"
        assert row.error == "EMPTY_ACTION_OUTPUT"
        assert json.loads(row.state_json)["executed_tool_fingerprints"] == []
    database.dispose()
