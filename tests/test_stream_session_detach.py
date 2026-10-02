"""流式对话在请求会话关闭后仍能写完回复。

2026-10-01 起，路由先取走会话编号再返回 SSE。FastAPI 随即关闭请求上的
Session，已加载的 Conversation 不再属于该 Session。收尾不能再 refresh 原对象。
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from sse_starlette.sse import AppStatus

from app.agents.pipeline import AgentEvent, AgentPipeline
from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.conversation import Message
from app.models.user import User
from app.services.chat_service import ChatService


def _user() -> User:
    return User(
        id="detach-user",
        tenant_id="tenant-detach",
        username="detach-user",
        hashed_password="",
        role=Role.MEMBER.value,
        token_version=0,
        is_active=True,
    )


async def _finish_immediately(self, state):  # noqa: ARG001
    state.answer = "已经写完"
    yield AgentEvent("token", "已经写完")
    yield AgentEvent("done", "")


def _assistant_rows(conversation_id: str) -> list[Message]:
    with Session(engine) as session:
        rows = list(
            session.exec(select(Message).where(Message.conversation_id == conversation_id)).all()
        )
    return [row for row in rows if row.role == "assistant"]


@pytest.mark.asyncio
async def test_stream_finishes_after_request_session_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    """交出会话编号后关闭请求 Session，剩余事件仍能结束并留下完整回复。"""
    monkeypatch.setattr(AgentPipeline, "run_stream", _finish_immediately)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT", False)
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self")
    set_llm_provider_override(MockLLMProvider())
    service = ChatService()
    with Session(engine) as session:
        generator = service.chat_stream(session, _user(), "请写完")
        first = await generator.__anext__()
        assert first.type == "conversation"
        conversation_id = first.data
        session.close()
        events = [event async for event in generator]
    assert [event.type for event in events if event.type == "error"] == []
    assert any(event.type == "done" for event in events)
    assistant = _assistant_rows(conversation_id)
    assert len(assistant) == 1
    assert assistant[0].content == "已经写完"
    assert assistant[0].status == "complete"


@pytest.mark.asyncio
async def test_stream_finishes_while_request_session_stays_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """请求 Session 仍持有该会话时，回复同样完整写完。"""
    monkeypatch.setattr(AgentPipeline, "run_stream", _finish_immediately)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT", False)
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self")
    set_llm_provider_override(MockLLMProvider())
    service = ChatService()
    with Session(engine) as session:
        generator = service.chat_stream(session, _user(), "会话还在")
        events = [event async for event in generator]
    conversation_id = next(event.data for event in events if event.type == "conversation")
    assert any(event.type == "done" for event in events)
    assistant = _assistant_rows(conversation_id)
    assert len(assistant) == 1
    assert assistant[0].status == "complete"


@pytest.fixture()
def client():
    app.dependency_overrides[get_current_user] = _user
    set_llm_provider_override(MockLLMProvider())
    with TestClient(app) as http:
        yield http
    app.dependency_overrides.clear()
    set_llm_provider_override(None)


def test_http_stream_does_not_report_failure_after_reply(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实路由下，回复写完后的事件流不包含生成失败。"""
    AppStatus.should_exit_event = None
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT", False)
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", "self")
    monkeypatch.setattr(AgentPipeline, "run_stream", _finish_immediately)
    with client.stream("POST", "/api/chat/stream", json={"message": "请写完"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    payloads = [
        json.loads(line[5:].strip())
        for line in body.splitlines()
        if line.startswith("data:") and line[5:].strip()
    ]
    assert all(item.get("data") != "生成失败，请稍后重试" for item in payloads)
    assert any(item.get("type") == "done" for item in payloads)
    conversation_id = next(item["data"] for item in payloads if item.get("type") == "conversation")
    assistant = _assistant_rows(conversation_id)
    assert len(assistant) == 1
    assert assistant[0].status == "complete"
