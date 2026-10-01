"""会话停止、断线可加载的状态、重命名删除和只读禁发。

身份范围：

- 成员只能看见、改名和删除自己在当前租户里的会话。
- 同一租户的另一名成员看不见，也不能改名或删除。
- 系统管理员能看见、改名和删除自己当前租户里的全部会话，看不见其他租户。
- 本文件不调用知识库接口，也不改变系统管理员的知识库跨租户列表。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.agents.pipeline import AgentEvent, AgentPipeline
from app.api.deps import get_current_user
from app.core.database import engine
from app.core.security import Role
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.conversation import Message
from app.models.user import User


def _user(user_id: str, tenant_id: str, role: Role, username: str) -> User:
    return User(
        id=user_id,
        tenant_id=tenant_id,
        username=username,
        hashed_password="",
        role=role.value,
        token_version=0,
        is_active=True,
    )


@pytest.fixture()
def client():
    holder = {
        "user": _user("chat-member", "tenant-a", Role.MEMBER, "chat-member"),
    }

    def current_user() -> User:
        return holder["user"]

    app.dependency_overrides[get_current_user] = current_user
    set_llm_provider_override(MockLLMProvider())
    with TestClient(app) as c:
        yield c, holder
    app.dependency_overrides.clear()
    set_llm_provider_override(None)


def test_viewer_cannot_send_and_member_can(client) -> None:
    """viewer 发送被拒绝。member 仍可发送。"""
    http, holder = client
    holder["user"] = _user("chat-viewer", "tenant-a", Role.VIEWER, "chat-viewer")
    denied = http.post("/api/chat", json={"message": "只读不能发"})
    assert denied.status_code == 403
    denied_stream = http.post("/api/chat/stream", json={"message": "只读不能发"})
    assert denied_stream.status_code == 403

    holder["user"] = _user("chat-member", "tenant-a", Role.MEMBER, "chat-member")
    allowed = http.post("/api/chat", json={"message": "成员可以发"})
    assert allowed.status_code == 200
    detail = http.get(f"/api/chat/conversations/{allowed.json()['conversation_id']}")
    assert detail.status_code == 200
    roles = [item["role"] for item in detail.json()["messages"]]
    assert roles[-1] == "assistant"
    assert detail.json()["messages"][-1]["status"] == "complete"


def test_stream_emits_conversation_id(client) -> None:
    """流的第一条业务事件带会话编号，结束后详情里是完整回复。"""
    http, _holder = client
    with http.stream("POST", "/api/chat/stream", json={"message": "流式编号"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert '"type": "conversation"' in body
    conversation_id = body.split('"type": "conversation"', 1)[1]
    assert "data" in conversation_id


def test_rename_and_delete_follow_owner_and_same_tenant_admin(client) -> None:
    """成员不能改、不能删别人的会话。当前租户的系统管理员可以。其他租户的管理员不行。"""
    http, holder = client
    created = http.post("/api/chat", json={"message": "原始标题"})
    assert created.status_code == 200
    conversation_id = created.json()["conversation_id"]

    blank = http.patch(f"/api/chat/conversations/{conversation_id}", json={"title": "   "})
    assert blank.status_code == 422
    assert blank.json()["detail"] == "标题不能为空"
    too_long = http.patch(
        f"/api/chat/conversations/{conversation_id}",
        json={"title": "名" * 81},
    )
    assert too_long.status_code == 422
    assert too_long.json()["detail"] == "标题不能超过 80 个字"

    holder["user"] = _user("chat-peer", "tenant-a", Role.MEMBER, "chat-peer")
    peer_rename = http.patch(
        f"/api/chat/conversations/{conversation_id}",
        json={"title": "别人改的"},
    )
    assert peer_rename.status_code == 404
    peer_delete = http.delete(f"/api/chat/conversations/{conversation_id}")
    assert peer_delete.status_code == 404
    peer_list = http.get("/api/chat/conversations")
    assert all(item["id"] != conversation_id for item in peer_list.json())
    assert http.get(f"/api/chat/conversations/{conversation_id}").status_code == 404

    holder["user"] = _user("chat-admin-b", "tenant-b", Role.SYSTEM_ADMIN, "chat-admin-b")
    other_tenant = http.get("/api/chat/conversations")
    assert all(item["id"] != conversation_id for item in other_tenant.json())
    assert http.get(f"/api/chat/conversations/{conversation_id}").status_code == 404

    holder["user"] = _user("chat-admin-a", "tenant-a", Role.SYSTEM_ADMIN, "chat-admin-a")
    visible = http.get("/api/chat/conversations")
    assert any(item["id"] == conversation_id for item in visible.json())
    renamed = http.patch(
        f"/api/chat/conversations/{conversation_id}",
        json={"title": "管理员改名"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "管理员改名"

    holder["user"] = _user("chat-member", "tenant-a", Role.MEMBER, "chat-member")
    own = http.get("/api/chat/conversations")
    assert any(item["id"] == conversation_id and item["title"] == "管理员改名" for item in own.json())

    holder["user"] = _user("chat-admin-a", "tenant-a", Role.SYSTEM_ADMIN, "chat-admin-a")
    deleted = http.delete(f"/api/chat/conversations/{conversation_id}")
    assert deleted.status_code == 200
    holder["user"] = _user("chat-member", "tenant-a", Role.MEMBER, "chat-member")
    assert http.get(f"/api/chat/conversations/{conversation_id}").status_code == 404


def test_same_tenant_members_cannot_see_each_other(client) -> None:
    """同一租户的两名成员互相看不见会话。"""
    http, holder = client
    mine = http.post("/api/chat", json={"message": "只属于成员甲"})
    conversation_id = mine.json()["conversation_id"]
    holder["user"] = _user("chat-other", "tenant-a", Role.MEMBER, "chat-other")
    listing = http.get("/api/chat/conversations")
    assert all(item["id"] != conversation_id for item in listing.json())
    assert http.get(f"/api/chat/conversations/{conversation_id}").status_code == 404


@pytest.mark.asyncio
async def test_stop_persists_partial_and_does_not_append() -> None:
    """停止后只留下已经产出的文字，状态为已停止，后文不会再写入。"""

    async def fake_run_stream(self, state):  # noqa: ARG001
        yield AgentEvent("token", "部分")
        await asyncio.Event().wait()
        yield AgentEvent("token", "不应出现")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(AgentPipeline, "run_stream", fake_run_stream)
    set_llm_provider_override(MockLLMProvider())
    user = _user("stop-user", "tenant-stop", Role.MEMBER, "stop-user")
    try:
        service_module = __import__("app.services.chat_service", fromlist=["ChatService"])
        service = service_module.ChatService()
        with Session(engine) as session:
            generator = service.chat_stream(session, user, "请停住")
            conversation_id = ""
            try:
                async for event in generator:
                    if event.type == "conversation":
                        conversation_id = event.data
                    if event.type == "token":
                        break
            finally:
                await generator.aclose()
        with Session(engine) as session:
            rows = list(
                session.exec(
                    select(Message).where(Message.conversation_id == conversation_id)
                ).all()
            )
        assistant = [row for row in rows if row.role == "assistant"]
        assert len(assistant) == 1
        assert assistant[0].content == "部分"
        assert "不应出现" not in assistant[0].content
        assert assistant[0].status == "stopped"
        assert any(row.role == "user" and row.content == "请停住" for row in rows)
    finally:
        monkeypatch.undo()
        set_llm_provider_override(None)
