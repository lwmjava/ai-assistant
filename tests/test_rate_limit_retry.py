"""限流等待秒数：超限给出秒数且不写新消息；速率为 0 时拒绝且不除零。"""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from sse_starlette.sse import AppStatus

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.security import reset_security_singletons
from app.security.rate_limiter import RateLimitConfig, RateLimiter
from app.security.types import SecurityContext


def _user_message_count(user_id: str) -> int:
    with Session(engine) as session:
        rows = session.exec(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.user_id == user_id)
            .where(Message.role == "user")
        ).all()
    return len(rows)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    user = User(
        id=f"rl-{uuid.uuid4().hex[:8]}",
        tenant_id=f"rl-tenant-{uuid.uuid4().hex[:8]}",
        username=f"rl-{uuid.uuid4().hex[:8]}",
        hashed_password="",
        role=Role.MEMBER.value,
        token_version=0,
        is_active=True,
    )
    monkeypatch.setattr("app.security.rate_limiter.time.monotonic", lambda: 1_000_000.0)
    monkeypatch.setattr(settings, "SECURITY_ENABLED", True)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT", True)
    monkeypatch.setattr(settings, "SECURITY_INPUT_FILTER", False)
    monkeypatch.setattr(settings, "SECURITY_INJECTION_DETECTION", False)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT_CAPACITY", 1.0)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT_RATE", 1.0)
    reset_security_singletons()
    app.dependency_overrides[get_current_user] = lambda: user
    set_llm_provider_override(MockLLMProvider())
    with TestClient(app) as http:
        yield http, user
    app.dependency_overrides.clear()
    set_llm_provider_override(None)
    reset_security_singletons()


def test_wait_seconds_when_rate_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.security.rate_limiter.time.monotonic", lambda: 1_000_000.0)
    limiter = RateLimiter(RateLimitConfig(rate=1.0, capacity=1.0, enabled=True))
    allowed_ctx = SecurityContext()
    assert limiter.allow("bucket", ctx=allowed_ctx)[0] is True
    assert allowed_ctx.retry_after_seconds is None
    denied = SecurityContext()
    assert limiter.allow("bucket", ctx=denied)[0] is False
    assert denied.retry_after_seconds == 1


def test_zero_rate_does_not_divide(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.security.rate_limiter.time.monotonic", lambda: 1_000_000.0)
    limiter = RateLimiter(RateLimitConfig(rate=0.0, capacity=1.0, enabled=True))
    assert limiter.allow("bucket")[0] is True
    denied = SecurityContext()
    assert limiter.allow("bucket", ctx=denied)[0] is False
    assert denied.retry_after_seconds is None


def test_second_chat_returns_wait_and_writes_no_message(client) -> None:
    http, user = client
    before = _user_message_count(user.id)
    first = http.post("/api/chat", json={"message": "第一条"})
    assert first.status_code == 200, first.text
    after_first = _user_message_count(user.id)
    assert after_first == before + 1

    second = http.post("/api/chat", json={"message": "第二条"})
    assert second.status_code == 429
    body = second.json()
    assert body["code"] == "rate_limited"
    assert body["retry_after_seconds"] == 1
    assert second.headers["retry-after"] == "1"
    assert _user_message_count(user.id) == after_first


def test_zero_rate_response_has_no_retry_after(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, user = client
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT_RATE", 0.0)
    reset_security_singletons()
    before = _user_message_count(user.id)
    first = http.post("/api/chat", json={"message": "第一条"})
    assert first.status_code == 200, first.text
    second = http.post("/api/chat", json={"message": "第二条"})
    assert second.status_code == 429
    assert second.json() == {"code": "rate_limited", "retry_after_seconds": None}
    assert "retry-after" not in second.headers
    assert _user_message_count(user.id) == before + 1


def test_under_limit_stream_has_no_rate_limit_event(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _user = client
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT_CAPACITY", 10.0)
    reset_security_singletons()
    AppStatus.should_exit_event = None
    with http.stream("POST", "/api/chat/stream", json={"message": "未超限"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: rate_limit" not in body


def test_stream_emits_rate_limit_before_error_and_writes_no_message(client) -> None:
    http, user = client
    AppStatus.should_exit_event = None
    with http.stream("POST", "/api/chat/stream", json={"message": "第一条"}) as resp:
        assert resp.status_code == 200
        "".join(resp.iter_text())
    before = _user_message_count(user.id)
    AppStatus.should_exit_event = None
    with http.stream("POST", "/api/chat/stream", json={"message": "第二条"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: rate_limit" in body
    assert '"retry_after_seconds": 1' in body or '"retry_after_seconds":1' in body
    assert "请求过于频繁" in body
    assert body.index("rate_limit") < body.index("请求过于频繁")
    assert _user_message_count(user.id) == before


def test_readme_states_single_process_limit() -> None:
    text = Path("README.md").read_text(encoding="utf-8")
    assert "完成 `OPS-001` 之前不要水平扩展" in text
    assert "重启即清空" in text
