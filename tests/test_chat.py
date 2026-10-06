"""对话接口集成测试。

通过依赖覆盖注入 Mock 提供商与合成用户，验证 /api/chat 非流式、
/api/chat/stream 流式、会话列表 / 详情 / 删除等端到端行为，不依赖真实大模型。
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.core.database import engine
from app.core.security import Role
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.conversation import Message
from app.models.user import User
from app.services.chat_service import ChatService


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


def test_chat_with_rag_enabled_completes(client: TestClient, monkeypatch) -> None:
    """RAG_ENABLED=true 时 Chat 主链可完成（Mock Embedding + Mock LLM）。"""
    from app.core.config import settings
    from app.rag.embeddings.factory import set_embedding_override
    from app.rag.embeddings.mock import MockEmbeddingProvider

    monkeypatch.setattr(settings, "RAG_ENABLED", True)
    set_embedding_override(MockEmbeddingProvider(dim=64))
    try:
        ingest = client.post(
            "/api/rag/documents/ingest",
            json={
                "text": "北风演示标准套餐月费为 199 元。",
                "title": "计费",
                "source": "chat-rag",
            },
        )
        assert ingest.status_code == 200
        resp = client.post("/api/chat", json={"message": "标准套餐月费是多少"})
        assert resp.status_code == 200
        assert resp.json()["reply"]
    finally:
        set_embedding_override(None)


def test_chat_reply_includes_source_filename(client: TestClient, monkeypatch) -> None:
    from app.core.config import settings
    from app.rag.embeddings.factory import set_embedding_override
    from app.rag.embeddings.mock import MockEmbeddingProvider

    monkeypatch.setattr(settings, "RAG_ENABLED", True)
    set_embedding_override(MockEmbeddingProvider(dim=64))
    try:
        ingest = client.post(
            "/api/rag/documents/ingest",
            json={
                "text": "来源演示文件写明标准套餐月费为 199 元。",
                "title": "计费说明",
                "source": "billing.txt",
            },
        )
        assert ingest.status_code == 200
        resp = client.post(
            "/api/chat", json={"message": "来源演示文件写明标准套餐月费为多少"}
        )
        assert resp.status_code == 200
        body = resp.json()
        matched = [item for item in body["sources"] if item["filename"] == "billing.txt"]
        assert matched
        assert matched[0]["page"] is None
        assert matched[0]["section"] is None

        detail = client.get(f"/api/chat/conversations/{body['conversation_id']}")
        assert detail.status_code == 200
        assistant = [item for item in detail.json()["messages"] if item["role"] == "assistant"]
        assert any(item["filename"] == "billing.txt" for item in assistant[-1]["sources"])
        user_messages = [item for item in detail.json()["messages"] if item["role"] == "user"]
        assert user_messages[-1]["sources"] == []
    finally:
        set_embedding_override(None)


def test_chat_sources_empty_when_retrieval_disabled(client: TestClient, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "RAG_ENABLED", False)
    resp = client.post("/api/chat", json={"message": "没有检索时不要带来源"})
    assert resp.status_code == 200
    assert resp.json()["sources"] == []


def test_chat_stream_includes_sources_event(client: TestClient, monkeypatch) -> None:
    from app.core.config import settings
    from app.rag.embeddings.factory import set_embedding_override
    from app.rag.embeddings.mock import MockEmbeddingProvider

    monkeypatch.setattr(settings, "RAG_ENABLED", True)
    set_embedding_override(MockEmbeddingProvider(dim=64))
    try:
        ingest = client.post(
            "/api/rag/documents/ingest",
            json={
                "text": "流式来源文件写明夜间套餐月费为 59 元。",
                "title": "夜间套餐",
                "source": "night.txt",
            },
        )
        assert ingest.status_code == 200
        with client.stream(
            "POST", "/api/chat/stream", json={"message": "流式来源文件里的夜间套餐月费是多少"}
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
        assert '"type": "sources"' in body
        assert "night.txt" in body
    finally:
        set_embedding_override(None)


def test_chat_stream_reports_unexpected_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routes import chat as chat_route

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("SECRET_DB_PATH")
        yield None

    monkeypatch.setattr(chat_route._service, "chat_stream", _boom)
    with client.stream("POST", "/api/chat/stream", json={"message": "流式测试"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "生成失败，请稍后重试" in body
    assert "SECRET_DB_PATH" not in body


def test_chat_stream_returns_sse(client: TestClient) -> None:
    with client.stream("POST", "/api/chat/stream", json={"message": "流式测试"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "data" in body


def test_chat_stream_midstream_failure_closes_generator(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.pipeline import AgentEvent
    from app.api.routes import chat as chat_route

    closed = []

    async def failing_stream(*args, **kwargs):
        try:
            yield AgentEvent("token", "first")
            raise RuntimeError("PRIVATE_MIDSTREAM_DETAILS")
        finally:
            closed.append(True)

    monkeypatch.setattr(chat_route._service, "chat_stream", failing_stream)
    with client.stream("POST", "/api/chat/stream", json={"message": "流式测试"}) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "first" in body
    assert "生成失败，请稍后重试" in body
    assert "PRIVATE_MIDSTREAM_DETAILS" not in body
    assert closed == [True]


_PATH_SENTINEL = r"C:\Users\secret-host\note.txt"


class _CodeResultLLM:
    """行动阶段调用一次代码工具，其余环节给出不含 NO 的短句。"""

    model = "script"

    def __init__(self) -> None:
        self.used = False

    async def chat(self, messages, options=None):
        content = messages[-1].content
        if "## 可用外部工具" in content and not self.used:
            self.used = True
            payload = json.dumps(
                {
                    "name": "code_sandbox",
                    "arguments": {"code": f"print(1)\n# {_PATH_SENTINEL}"},
                },
                ensure_ascii=False,
            )
            return f"<tool_call>{payload}</tool_call>"
        return "完成。"

    async def stream_chat(self, messages, options=None):
        yield "计算结果是 1。"


def _code_result_rows(body: str) -> list[dict]:
    rows: list[dict] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw:
            continue
        payload = json.loads(raw)
        if payload.get("type") == "code_result":
            rows.append(payload["data"])
    return rows


def test_code_result_persists_on_complete(client: TestClient) -> None:
    """一次性对话的响应和会话详情都带上标准输出，且没有宿主机路径。"""
    set_llm_provider_override(_CodeResultLLM())
    resp = client.post("/api/chat", json={"message": "请运行这段代码"})
    assert resp.status_code == 200
    body = resp.json()
    assert _PATH_SENTINEL not in resp.text
    assert body["code_results"][0]["status"] == "ok"
    assert body["code_results"][0]["stdout"].strip() == "1"
    detail = client.get(f"/api/chat/conversations/{body['conversation_id']}")
    assert detail.status_code == 200
    assistant = [item for item in detail.json()["messages"] if item["role"] == "assistant"]
    assert assistant[-1]["code_results"][0]["stdout"].strip() == "1"
    assert assistant[-1]["status"] == "complete"
    assert _PATH_SENTINEL not in detail.text


def test_code_result_persists_on_stream(client: TestClient) -> None:
    """流式事件和刷新后的消息都有同一次标准输出。"""
    set_llm_provider_override(_CodeResultLLM())
    with client.stream("POST", "/api/chat/stream", json={"message": "请运行这段代码"}) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    assert _PATH_SENTINEL not in raw
    results = _code_result_rows(raw)
    assert results[0]["status"] == "ok"
    assert results[0]["stdout"].strip() == "1"
    conversation_id = ""
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        payload = json.loads(line[5:].strip())
        if payload.get("type") == "conversation":
            conversation_id = payload["data"]
    detail = client.get(f"/api/chat/conversations/{conversation_id}")
    assistant = [item for item in detail.json()["messages"] if item["role"] == "assistant"]
    assert assistant[-1]["code_results"][0]["stdout"].strip() == "1"


@pytest.mark.asyncio
async def test_code_result_persists_when_stopped(client: TestClient) -> None:
    """生成在最终回复前停下时，已经跑完的代码结果仍写在停止消息上。"""

    class _Blocking(_CodeResultLLM):
        async def stream_chat(self, messages, options=None):
            await asyncio.Event().wait()
            yield "不应出现"

    set_llm_provider_override(_Blocking())
    user = User(
        id="code-stop-user",
        tenant_id="test-tenant",
        username="code-stop",
        hashed_password="",
        role=Role.MEMBER.value,
        token_version=0,
        is_active=True,
    )
    service = ChatService()
    conversation_id = ""
    with Session(engine) as session:
        generator = service.chat_stream(session, user, "请运行后停住")
        try:
            async for event in generator:
                if event.type == "conversation":
                    conversation_id = event.data
                if event.type == "code_result":
                    break
        finally:
            await generator.aclose()
    with Session(engine) as session:
        rows = list(
            session.exec(select(Message).where(Message.conversation_id == conversation_id)).all()
        )
    assistant = [row for row in rows if row.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].status == "stopped"
    saved = json.loads(assistant[0].code_results or "[]")
    assert saved[0]["status"] == "ok"
    assert saved[0]["stdout"].strip() == "1"
    assert _PATH_SENTINEL not in (assistant[0].code_results or "")
    assert client is not None
