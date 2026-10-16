"""Typed failures must survive orchestration and persisted chat boundaries."""

import asyncio
import base64
from types import SimpleNamespace

import pytest
from sqlmodel import Session, col, select

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.supervisor import SupervisorGraph
from app.core.database import engine
from app.llm.budget import ContextBudgetError
from app.llm.mock import MockLLMProvider
from app.llm.routing import LLMUnavailableError
from app.models.conversation import Message
from app.services import chat_attachments, chat_service
from tests.test_chat_attachments import attachment_client as _attachment_client

attachment_client = _attachment_client


class UnavailableModel(MockLLMProvider):
    async def chat(self, messages, options=None):
        raise LLMUnavailableError()

    async def stream_chat(self, messages, options=None):
        raise LLMUnavailableError()
        yield "unreachable"


def test_pipeline_preserves_typed_model_failure():
    state = asyncio.run(AgentPipeline(UnavailableModel()).run(AgentState(user_input="question")))
    assert getattr(state, "error_code", None) == "model_unavailable"


def test_pipeline_does_not_classify_arbitrary_exception_prose_as_model_outage():
    class InternalFailure(MockLLMProvider):
        async def chat(self, messages, options=None):
            raise RuntimeError("model_unavailable credential=PRIVATE_INTERNAL_DETAIL")

    state = asyncio.run(AgentPipeline(InternalFailure()).run(AgentState(user_input="question")))
    assert state.error_code == "generation_failed"
    assert "PRIVATE_INTERNAL_DETAIL" not in state.answer


def test_pipeline_keeps_budget_rejection_distinct_from_model_outage():
    class BudgetFailure(MockLLMProvider):
        async def chat(self, messages, options=None):
            raise ContextBudgetError("context_window_exceeded", payload_tokens=100,
                                     reserved_output=1, margin=1, limit=1)

    state = asyncio.run(AgentPipeline(BudgetFailure()).run(AgentState(user_input="question")))
    assert state.error_code == "context_budget_exceeded"
    assert "缩短输入" in state.answer


def test_supervisor_preserves_typed_model_failure():
    async def fail(_state):
        raise LLMUnavailableError()

    graph = SupervisorGraph.__new__(SupervisorGraph)
    graph.retriever = None
    graph._last_outcome = None
    graph._graph = SimpleNamespace(ainvoke=fail)
    state = asyncio.run(graph.run(AgentState(user_input="question")))
    assert getattr(state, "error_code", None) == "model_unavailable"


@pytest.mark.parametrize("fast", [True, False])
def test_chat_model_failure_persists_failed_assistant_and_raises_typed_error(attachment_client, monkeypatch, fast):
    _client, user, _model = attachment_client
    service = chat_service.ChatService(UnavailableModel())
    if not fast:
        monkeypatch.setattr(service, "_fast_route", lambda _message: None)
    with Session(engine) as session:
        try:
            asyncio.run(service.chat(session, user, "你好"))
        except Exception as exc:
            assert type(exc).__name__ == "ModelUnavailableError"
        else:
            pytest.fail("Model outage must not return a successful chat")
    # Read committed data through a new session, rather than the mutated state object.
    with Session(engine) as session:
        from app.models.conversation import Conversation

        conv = session.exec(select(Conversation).where(col(Conversation.user_id) == user.id)).one()
        messages = session.exec(select(Message).where(col(Message.conversation_id) == conv.id)).all()
        assert len([message for message in messages if message.role == "user"]) == 1
        assistants = [message for message in messages if message.role == "assistant"]
        assert len(assistants) == 1
        assert assistants[0].status == "failed"
        assert "LLMUnavailableError" not in assistants[0].content


@pytest.mark.parametrize("size", [1048577, 1048580])
def test_attachment_overflow_has_distinct_typed_error(size):
    with pytest.raises(chat_attachments.AttachmentValidationError) as caught:
        chat_attachments.prepare_attachments([{
            "name": "sample.py", "content_base64": base64.b64encode(b"x" * size).decode(),
        }])
    assert type(caught.value).__name__ == "AttachmentTooLargeError"


def test_empty_attachment_remains_validation_error_and_exact_limit_is_allowed():
    with pytest.raises(chat_attachments.AttachmentValidationError) as caught:
        chat_attachments.prepare_attachments([{"name": "sample.py", "content_base64": ""}])
    assert type(caught.value).__name__ == "AttachmentValidationError"
    prepared = chat_attachments.prepare_attachments([{
        "name": "sample.py", "content_base64": base64.b64encode(b"x" * 1048576).decode(),
    }])
    assert prepared[0].size == 1048576
