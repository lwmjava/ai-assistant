"""QUOTA-002：Token 上限、自然月重置、超限含 resets_at。"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import engine
from app.core.security import Role
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.user import Tenant, User
from app.services.quota import (
    QuotaExceededError,
    accept_user_message,
    consume_tokens,
    current_period_ym,
    ensure_quota_period,
    next_month_resets_at,
)


def _user(tenant_id: str, role: str, user_id: str | None = None) -> User:
    return User(
        id=user_id or f"user-{tenant_id}",
        tenant_id=tenant_id,
        username=f"name-{tenant_id}-{role}",
        hashed_password="",
        role=role,
        token_version=0,
        is_active=True,
    )


def _tenant(*, name: str, token_limit: int | None = None, tokens_used: int = 0) -> Tenant:
    with Session(engine) as session:
        tenant = Tenant(
            name=name,
            token_limit=token_limit,
            tokens_used_period=tokens_used,
            quota_period_ym=current_period_ym(),
        )
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        session.expunge(tenant)
        return tenant


@pytest.fixture()
def client(persist_identity):
    holder: dict[str, User | TestClient] = {}

    def _override():
        return holder["user"]

    app.dependency_overrides[get_current_user] = _override
    set_llm_provider_override(MockLLMProvider())
    http = TestClient(app)
    holder["client"] = http

    def bound(user: User) -> TestClient:
        holder["user"] = user
        return http

    yield bound
    app.dependency_overrides.pop(get_current_user, None)
    set_llm_provider_override(None)


def test_token_limit_blocks_before_writing_message() -> None:
    tenant = _tenant(name="quota-token-block", token_limit=10, tokens_used=10)
    with Session(engine) as session:
        with pytest.raises(QuotaExceededError) as raised:
            accept_user_message(
                session,
                tenant_id=tenant.id,
                user_id="u1",
                role=Role.MEMBER.value,
                conversation_id=None,
                message="hello",
            )
    assert raised.value.limit_type == "tokens"
    assert raised.value.used == 10
    assert raised.value.limit == 10
    assert raised.value.resets_at == next_month_resets_at()
    body = raised.value.as_dict()
    assert body["resets_at"] == raised.value.resets_at
    with Session(engine) as session:
        row = session.get(Tenant, tenant.id)
        assert row is not None
        assert row.tokens_used_period == 10


def test_natural_month_resets_token_usage() -> None:
    tenant = _tenant(name="quota-token-reset", token_limit=5, tokens_used=5)
    with Session(engine) as session:
        row = session.get(Tenant, tenant.id)
        assert row is not None
        row.quota_period_ym = "2020-01"
        session.add(row)
        session.commit()

    with Session(engine) as session:
        row = session.get(Tenant, tenant.id)
        assert row is not None
        assert ensure_quota_period(row, now=datetime(2026, 10, 22, tzinfo=UTC)) is True
        assert row.quota_period_ym == "2026-10"
        assert row.tokens_used_period == 0
        session.add(row)
        session.commit()

    with Session(engine) as session:
        conv, _mid = accept_user_message(
            session,
            tenant_id=tenant.id,
            user_id="u-reset",
            role=Role.MEMBER.value,
            conversation_id=None,
            message="after reset",
        )
        assert conv.id
        consume_tokens(session, tenant.id, 3)
    with Session(engine) as session:
        row = session.get(Tenant, tenant.id)
        assert row is not None
        assert row.tokens_used_period == 3
        assert row.quota_period_ym == current_period_ym()


def test_chat_returns_resets_at_when_token_exhausted(client) -> None:
    tenant = _tenant(name="quota-token-chat", token_limit=1, tokens_used=1)
    http = client(_user(tenant.id, Role.MEMBER.value, user_id="quota-token-chat-user"))
    first = http.post("/api/v1/chat", json={"message": "blocked"})
    assert first.status_code == 429
    payload = first.json()
    assert payload["code"] == "quota_exceeded"
    assert payload["limit_type"] == "tokens"
    assert payload["used"] == 1
    assert payload["limit"] == 1
    assert isinstance(payload["resets_at"], str) and payload["resets_at"]
    assert payload["resets_at"] == next_month_resets_at()
