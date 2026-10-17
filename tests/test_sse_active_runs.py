"""运行事件具有稳定身份与回放位置，查询不重新执行对话。"""

import json
import socket

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import Role
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.user import User
from app.rag.embeddings.factory import set_embedding_override
from app.rag.embeddings.mock import MockEmbeddingProvider


@pytest.fixture
def active_client(persist_identity, monkeypatch):
    original_connect = socket.socket.connect

    def local_only(connection, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("测试禁止外网连接")
        return original_connect(connection, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)
    user = User(id="active-owner", tenant_id="active-tenant", username="active-owner",
                hashed_password="", role=Role.MEMBER.value)
    persist_identity(user)
    app.dependency_overrides[get_current_user] = lambda: user
    set_llm_provider_override(MockLLMProvider())
    set_embedding_override(MockEmbeddingProvider(dim=64))
    try:
        with TestClient(app) as client:
            yield client
    finally:
        set_llm_provider_override(None)
        set_embedding_override(None)


def _events(body):
    result = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        fields = {}
        for line in block.splitlines():
            key, _, value = line.partition(":")
            fields[key] = value.lstrip()
        if "data" in fields:
            fields["payload"] = json.loads(fields["data"])
            result.append(fields)
    return result


def test_stream_emits_stable_run_id_and_ordered_event_ids(active_client):
    response = active_client.post("/api/v1/chat/stream", json={"message": "运行协议验证"})
    assert response.status_code == 200
    events = _events(response.text)
    names = {event["event"] for event in events}
    assert {"run.started", "node.started", "node.finished", "token", "run.finished"}.issubset(names)
    run_id = events[0]["payload"]["run_id"]
    assert run_id
    for sequence, event in enumerate(events, 1):
        assert event["id"] == f"{run_id}:{sequence}"
        assert event["payload"]["run_id"] == run_id
    token = next(event for event in events if event["event"] == "token")
    assert isinstance(token["payload"]["delta"], str)


def test_completed_run_can_be_loaded_and_replayed_without_resubmission(active_client):
    response = active_client.post("/api/v1/chat/stream", json={"message": "完成后回放"})
    events = _events(response.text)
    first = events[0]
    assert "run_id" in first["payload"], "首事件必须提供可查询的运行身份"
    run_id = first["payload"]["run_id"]
    loaded = active_client.get(f"/api/v1/runs/{run_id}")
    assert loaded.status_code == 200
    final = loaded.json()
    assert final["run_id"] == run_id
    assert final["status"] == "done"
    assert final["conversation_id"]
    assert final["message_id"]
    assert final["reply"]
    replayed = active_client.get(f"/api/v1/runs/{run_id}/events", headers={"Last-Event-ID": first["id"]})
    assert replayed.status_code == 200
    assert [event["id"] for event in _events(replayed.text)] == [event["id"] for event in events[1:]]


def test_disabled_auth_demo_stream_uses_exact_sentinel_and_closes_when_auth_returns(active_client, monkeypatch):
    from sqlmodel import Session

    from app.api.deps import _DISABLED_AUTH_PRINCIPAL
    from app.core.database import engine
    from app.models.user import Tenant

    app.dependency_overrides.pop(get_current_user)
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    response = active_client.post("/api/v1/chat/stream", json={"message": "演示模式流式对话"})
    assert response.status_code == 200
    events = _events(response.text)
    assert any(event["event"] == "run.finished" for event in events)
    run_id = events[0]["payload"]["run_id"]
    loaded = active_client.get(f"/api/v1/runs/{run_id}")
    assert loaded.status_code == 200
    assert loaded.json()["status"] == "done"
    assert loaded.json()["reply"]
    with Session(engine) as session:
        assert session.get(User, "__auth_disabled__") is None
        assert session.get(Tenant, "__auth_disabled__") is None
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    app.dependency_overrides[get_current_user] = lambda: _DISABLED_AUTH_PRINCIPAL
    assert active_client.get(f"/api/v1/runs/{run_id}").status_code == 404


def test_disabled_auth_does_not_accept_non_sentinel_subject(active_client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    forged = User(id="missing-subject", tenant_id="__auth_disabled__", username="forged",
                  hashed_password="", role=Role.SYSTEM_ADMIN.value)
    app.dependency_overrides[get_current_user] = lambda: forged
    response = active_client.post("/api/v1/chat/stream", json={"message": "未知身份不能复用演示例外"})
    assert response.status_code == 404
