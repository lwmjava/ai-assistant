"""Cancellation is enforced inside an in-flight model call."""

import asyncio
import threading

import pytest

from app.llm.mock import MockLLMProvider
from app.services.evaluation_orchestrator import _BudgetedLLM, _ExternalBudget


@pytest.mark.asyncio
async def test_isolated_agent_uses_shared_route_and_skill_assembly(monkeypatch):
    from sqlmodel import Session, SQLModel, create_engine, select

    from app.agents.tools.base import ToolRegistry
    from app.models.conversation import Conversation
    from app.models.user import User
    from app.services import chat_service, evaluation_orchestrator

    calls = []
    monkeypatch.setattr(evaluation_orchestrator, "llm_availability", lambda: "real")
    monkeypatch.setattr(chat_service, "get_llm_provider", lambda *args: MockLLMProvider())

    async def match(self, session, user, query):
        calls.append(("match", user.id, query))
        return None

    async def tools(self):
        return ToolRegistry()

    async def selected(self, session, user, state, retriever, tools, ctx, runtime, **kwargs):
        assert runtime is not None
        assert isinstance(self.llm, _BudgetedLLM)
        assert isinstance(self._intent_llm(), _BudgetedLLM)
        assert callable(kwargs["prepare_pipeline"])
        calls.append(("shared", user.id, state.user_input))
        state.answer = "completed"
        return state

    monkeypatch.setattr(chat_service.ChatService, "_match_skills_async", match)
    monkeypatch.setattr(chat_service.ChatService, "_build_tools", tools)
    monkeypatch.setattr(chat_service.ChatService, "_build_retriever", lambda *args: None)
    monkeypatch.setattr(chat_service.ChatService, "_run_selected_agent", selected)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            user = User(id="user", tenant_id="tenant", username="user", hashed_password="", role="member")
            result = await evaluation_orchestrator._run_isolated_agent(
                session, user, "request", _ExternalBudget(5),
            )
            assert result.answer == "completed"
            assert calls == [("match", "user", "request"), ("shared", "user", "request")]
            assert session.exec(select(Conversation)).all() == []
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_cancel_inflight_call_closes_provider_and_blocks_next_call():
    started = asyncio.Event()
    stopped = asyncio.Event()
    cancelled = threading.Event()

    class SlowModel(MockLLMProvider):
        async def chat(self, messages, options=None):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    def check():
        if cancelled.is_set():
            raise asyncio.CancelledError

    budget = _ExternalBudget(10, check=check)
    model = _BudgetedLLM(SlowModel(), budget)
    task = asyncio.create_task(model.chat([]))
    await asyncio.wait_for(started.wait(), 1)
    cancelled.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert stopped.is_set()
    with pytest.raises(asyncio.CancelledError):
        await model.chat([])
    assert budget.used == 1
