"""对话接口集成测试。

通过依赖覆盖注入 Mock 提供商与合成用户，验证 /api/chat 非流式、
/api/chat/stream 流式、会话列表 / 详情 / 删除等端到端行为，不依赖真实大模型。
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.security import Role
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.user import User


@pytest.fixture()
def client():
    fake_user = User(
        id="test-user",
        tenant_id="test-tenant",
        username="tester",
        hashed_password="",
        role=Role.MEMBER.value,
        token_version=0,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user
    set_llm_provider_override(MockLLMProvider())
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    set_llm_provider_override(None)


def test_chat_returns_reply(client: TestClient) -> None:
    resp = client.post("/api/chat", json={"message": "你好"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"]
    assert body["conversation_id"]


def test_empty_message_rejected(client: TestClient) -> None:
    resp = client.post("/api/chat", json={"message": "   "})
    assert resp.status_code == 400


def test_conversation_lifecycle(client: TestClient) -> None:
    created = client.post("/api/chat", json={"message": "测试会话生命周期"})
    cid = created.json()["conversation_id"]

    listing = client.get("/api/chat/conversations")
    assert listing.status_code == 200
    assert any(c["id"] == cid for c in listing.json())

    detail = client.get(f"/api/chat/conversations/{cid}")
    assert detail.status_code == 200
    assert detail.json()["messages"]

    deleted = client.delete(f"/api/chat/conversations/{cid}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_chat_stream_returns_sse(client: TestClient) -> None:
    with client.stream("POST", "/api/chat/stream", json={"message": "流式测试"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "data" in body
