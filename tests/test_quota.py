"""租户消息条数与源文件字节配额。使用测试库，降级用例单独用一块临时 SQLite。"""

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, func, select

from app.api.deps import get_current_user
from app.audit.models import AuditLog
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.conversation import Conversation, Message
from app.models.rag import Document, ImportJob
from app.models.user import Tenant, User
from app.rag.import_jobs import _process_job
from app.rag.service import RAGService
from app.services.quota import (
    QuotaExceededError,
    SourceFileUnreadableError,
    accept_user_message,
    begin_source_quota,
    used_source_bytes,
)
from app.workflow.bridge import WorkflowBridge

ROOT = Path(__file__).resolve().parents[1]


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


def _tenant(
    *,
    name: str,
    message_limit: int | None = None,
    storage_limit_bytes: int | None = None,
) -> Tenant:
    with Session(engine) as session:
        tenant = Tenant(
            name=name,
            message_limit=message_limit,
            storage_limit_bytes=storage_limit_bytes,
        )
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        session.expunge(tenant)
        return tenant


def _counts(tenant_id: str) -> tuple[int, int]:
    with Session(engine) as session:
        conversations = session.exec(
            select(func.count()).select_from(Conversation).where(col(Conversation.tenant_id) == tenant_id)
        ).one()
        messages = session.exec(
            select(func.count())
            .select_from(Message)
            .join(Conversation, col(Message.conversation_id) == Conversation.id)
            .where(col(Conversation.tenant_id) == tenant_id, col(Message.role) == "user")
        ).one()
    return int(conversations), int(messages)


def _files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def _patch_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.rag.document_storage as storage_mod

    monkeypatch.setattr(storage_mod, "_PROJECT_ROOT", tmp_path)


@pytest.fixture()
def client():
    holder: dict[str, User | TestClient] = {}

    def _open(user: User) -> TestClient:
        previous = holder.get("client")
        if isinstance(previous, TestClient):
            previous.__exit__(None, None, None)
        holder["user"] = user
        app.dependency_overrides[get_current_user] = lambda: user
        set_llm_provider_override(MockLLMProvider())
        opened = TestClient(app)
        opened.__enter__()
        holder["client"] = opened
        return opened

    yield _open
    opened = holder.get("client")
    if isinstance(opened, TestClient):
        opened.__exit__(None, None, None)
    app.dependency_overrides.clear()
    set_llm_provider_override(None)


def test_null_limits_leave_chat_and_text_ingest_open(client) -> None:
    tenant = _tenant(name="quota-null")
    http = client(_user(tenant.id, Role.MEMBER.value))
    chat = http.post("/api/chat", json={"message": "未设上限"})
    assert chat.status_code == 200
    ingested = http.post(
        "/api/rag/documents/ingest",
        json={"title": "纯文本", "text": "这段文字不写源文件。"},
    )
    assert ingested.status_code == 200
    limited = _tenant(name="quota-text-zero", storage_limit_bytes=0)
    http_limited = client(_user(limited.id, Role.MEMBER.value))
    still_open = http_limited.post(
        "/api/rag/documents/ingest",
        json={"title": "零字节上限", "text": "纯文本摄取不占用源文件额度。"},
    )
    assert still_open.status_code == 200
    with Session(engine) as session:
        row = session.get(Tenant, tenant.id)
        assert row is not None
        assert row.message_limit is None
        assert row.storage_limit_bytes is None


def test_message_limit_blocks_the_next_send_and_recovers_after_delete(client) -> None:
    tenant = _tenant(name="quota-messages", message_limit=1)
    http = client(_user(tenant.id, Role.MEMBER.value))
    first = http.post("/api/chat", json={"message": "第一条"})
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]
    before = _counts(tenant.id)

    blocked = http.post(
        "/api/chat",
        json={"message": "第二条", "conversation_id": conversation_id},
    )
    assert blocked.status_code == 429
    assert blocked.json() == {
        "code": "quota_exceeded",
        "limit_type": "messages",
        "used": 1,
        "limit": 1,
    }
    assert _counts(tenant.id) == before

    streamed = http.post("/api/chat/stream", json={"message": "流式也要拦住"})
    assert streamed.status_code == 429
    assert streamed.json()["limit_type"] == "messages"
    assert "text/event-stream" not in streamed.headers.get("content-type", "")
    assert _counts(tenant.id) == before

    deleted = http.delete(f"/api/chat/conversations/{conversation_id}")
    assert deleted.status_code == 200
    again = http.post("/api/chat", json={"message": "删掉之后可以再发"})
    assert again.status_code == 200
    assert _counts(tenant.id) == (1, 1)


def test_workflow_uses_the_same_message_quota() -> None:
    tenant = _tenant(name="quota-workflow", message_limit=0)
    user = _user(tenant.id, Role.MEMBER.value)

    async def run() -> None:
        with Session(engine) as session:
            await WorkflowBridge().execute(session, user, "工作流提问")

    with pytest.raises(QuotaExceededError):
        asyncio.run(run())
    assert _counts(tenant.id) == (0, 0)


def test_upload_and_batch_stop_before_writing_files(client, monkeypatch, tmp_path: Path) -> None:
    _patch_storage(monkeypatch, tmp_path)
    tenant = _tenant(name="quota-bytes", storage_limit_bytes=10)
    http = client(_user(tenant.id, Role.MEMBER.value))
    with Session(engine) as session:
        before_docs = int(
            session.exec(
                select(func.count()).select_from(Document).where(col(Document.tenant_id) == tenant.id)
            ).one()
        )

    blocked = http.post(
        "/api/rag/documents/upload",
        files={"file": ("too-big.txt", b"12345678901", "text/plain")},
    )
    assert blocked.status_code == 429
    assert blocked.json() == {
        "code": "quota_exceeded",
        "limit_type": "source_bytes",
        "used": 0,
        "limit": 10,
    }
    assert _files(tmp_path) == []

    batch = http.post(
        "/api/rag/import-jobs/upload",
        files=[
            ("files", ("a.txt", b"12345678", "text/plain")),
            ("files", ("b.txt", b"12345678", "text/plain")),
        ],
    )
    assert batch.status_code == 429
    assert batch.json()["limit_type"] == "source_bytes"
    assert _files(tmp_path) == []
    with Session(engine) as session:
        after_docs = int(
            session.exec(
                select(func.count()).select_from(Document).where(col(Document.tenant_id) == tenant.id)
            ).one()
        )
    assert after_docs == before_docs

    allowed = http.post(
        "/api/rag/documents/upload",
        files={"file": ("ok.txt", b"1234", "text/plain")},
    )
    assert allowed.status_code == 200
    document_id = allowed.json()["id"]
    with Session(engine) as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.source_bytes == 4
        assert used_source_bytes(session, tenant.id) == 4
        relative = document.storage_path
    assert relative is not None

    soft = http.delete(f"/api/rag/documents/{document_id}")
    assert soft.status_code == 200
    with Session(engine) as session:
        assert used_source_bytes(session, tenant.id) == 4
    (tmp_path / relative).unlink()
    with Session(engine) as session:
        assert used_source_bytes(session, tenant.id) == 0


def test_url_over_limit_leaves_no_file(client, monkeypatch, tmp_path: Path) -> None:
    _patch_storage(monkeypatch, tmp_path)
    tenant = _tenant(name="quota-url", storage_limit_bytes=8)
    http = client(_user(tenant.id, Role.MEMBER.value))

    class FakeResponse:
        content = b"this is longer than eight"
        headers = {"content-type": "text/plain"}

        def raise_for_status(self) -> None:
            return None

    async def fake_get(self, url: str, follow_redirects: bool = True):
        return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    created = http.post("/api/rag/import-jobs/url", json={"url": "https://example.com/quota"})
    assert created.status_code == 202
    with Session(engine) as session:
        job = session.get(ImportJob, created.json()["id"])
        assert job is not None
        asyncio.run(_process_job(session, job))
        session.refresh(job)
        assert job.status == "failed"
        assert job.storage_path is None
    assert _files(tmp_path) == []


def test_failed_ingest_removes_the_new_file(client, monkeypatch, tmp_path: Path) -> None:
    _patch_storage(monkeypatch, tmp_path)
    tenant = _tenant(name="quota-ingest-fail", storage_limit_bytes=1000)
    http = client(_user(tenant.id, Role.MEMBER.value))

    async def explode(self, *args, **kwargs):
        raise RuntimeError("摄取失败")

    monkeypatch.setattr(RAGService, "ingest_parsed_document", explode)
    failed = http.post(
        "/api/rag/documents/upload",
        files={"file": ("fail.txt", b"hello", "text/plain")},
    )
    assert failed.status_code == 500
    assert failed.json()["detail"] == "文档已解析，但知识库摄取失败，请稍后重试或联系管理员"
    assert _files(tmp_path) == []


def test_unreadable_existing_source_rejects_upload(client, monkeypatch, tmp_path: Path) -> None:
    _patch_storage(monkeypatch, tmp_path)
    tenant = _tenant(name="quota-unreadable", storage_limit_bytes=100)
    with Session(engine) as session:
        session.add(
            Document(
                tenant_id=tenant.id,
                user_id="reader",
                title="旧文件",
                storage_path="data/knowledge/old.txt",
            )
        )
        session.commit()

    def unreadable(relative_path: str) -> int:
        raise SourceFileUnreadableError(relative_path)

    monkeypatch.setattr("app.services.quota.source_file_size", unreadable)
    http = client(_user(tenant.id, Role.MEMBER.value))
    response = http.post(
        "/api/rag/documents/upload",
        files={"file": ("new.txt", b"hi", "text/plain")},
    )
    assert response.status_code == 503
    assert _files(tmp_path) == []
    with Session(engine) as session:
        titles = session.exec(select(Document.title).where(col(Document.tenant_id) == tenant.id)).all()
    assert list(titles) == ["旧文件"]


def test_quota_settings_require_admin_reason_and_audit(client, monkeypatch) -> None:
    tenant = _tenant(name="quota-admin", message_limit=3, storage_limit_bytes=9)
    member = client(_user(tenant.id, Role.MEMBER.value, user_id="quota-member"))
    denied = member.patch(
        f"/api/admin/tenants/{tenant.id}/quota",
        json={"message_limit": 1, "storage_limit_bytes": 1, "reason": "成员不能改"},
    )
    assert denied.status_code == 403

    admin = client(_user(tenant.id, Role.SYSTEM_ADMIN.value, user_id="quota-admin-user"))
    missing = admin.patch(
        f"/api/admin/tenants/{tenant.id}/quota",
        json={"message_limit": 1, "storage_limit_bytes": 1},
    )
    assert missing.status_code == 422
    negative = admin.patch(
        f"/api/admin/tenants/{tenant.id}/quota",
        json={"message_limit": -1, "storage_limit_bytes": 1, "reason": "负数"},
    )
    assert negative.status_code == 422
    with Session(engine) as session:
        row = session.get(Tenant, tenant.id)
        assert row is not None
        assert row.message_limit == 3
        assert row.storage_limit_bytes == 9

    updated = admin.patch(
        f"/api/admin/tenants/{tenant.id}/quota",
        json={"message_limit": 4, "storage_limit_bytes": None, "reason": " 放宽消息上限 "},
    )
    assert updated.status_code == 200
    assert updated.json()["message_limit"] == 4
    assert updated.json()["storage_limit_bytes"] is None
    with Session(engine) as session:
        logs = session.exec(
            select(AuditLog).where(
                col(AuditLog.resource_id) == tenant.id,
                col(AuditLog.action) == "quota_update",
            )
        ).all()
    assert len(logs) == 1
    details = logs[0].details
    if isinstance(details, str):
        details = json.loads(details)
    assert isinstance(details, dict)
    assert details["reason"] == "放宽消息上限"
    assert details["message_limit_old"] == 3
    assert details["message_limit_new"] == 4
    assert details["actor_id"] == "quota-admin-user"
    assert "content" not in details

    monkeypatch.setattr(settings, "AUDIT_ENABLED", False)
    closed = admin.patch(
        f"/api/admin/tenants/{tenant.id}/quota",
        json={"message_limit": 1, "storage_limit_bytes": 1, "reason": "审计关闭"},
    )
    assert closed.status_code == 503
    with Session(engine) as session:
        row = session.get(Tenant, tenant.id)
        assert row is not None
        assert row.message_limit == 4
        assert row.storage_limit_bytes is None

    monkeypatch.setattr(settings, "AUDIT_ENABLED", True)

    class BrokenAudit:
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("audit down")

    monkeypatch.setattr("app.services.quota.AuditLog", BrokenAudit)
    failed = admin.patch(
        f"/api/admin/tenants/{tenant.id}/quota",
        json={"message_limit": 2, "storage_limit_bytes": 2, "reason": "审计失败"},
    )
    assert failed.status_code == 503
    with Session(engine) as session:
        row = session.get(Tenant, tenant.id)
        assert row is not None
        assert row.message_limit == 4
        assert row.storage_limit_bytes is None


def test_concurrent_messages_and_source_bytes_stay_within_limit(monkeypatch, tmp_path: Path) -> None:
    _patch_storage(monkeypatch, tmp_path)
    messages = _tenant(name="quota-concurrent-msg", message_limit=1)
    storage = _tenant(name="quota-concurrent-bytes", storage_limit_bytes=10)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def send() -> None:
        barrier.wait()
        with Session(engine) as session:
            try:
                accept_user_message(
                    session,
                    tenant_id=messages.id,
                    user_id="concurrent-user",
                    role=Role.MEMBER.value,
                    conversation_id=None,
                    message="并发",
                )
            except QuotaExceededError:
                outcomes.append("blocked")
            else:
                outcomes.append("ok")

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert outcomes.count("ok") == 1
    assert _counts(messages.id) == (1, 1)

    byte_outcomes: list[str] = []
    byte_barrier = threading.Barrier(2)

    def write_file(name: str) -> None:
        byte_barrier.wait()
        with Session(engine) as session:
            try:
                from app.rag.document_storage import save_source_file

                begin_source_quota(session, storage.id, 8)
                path = save_source_file(storage.id, b"12345678", name)
                session.add(
                    Document(
                        tenant_id=storage.id,
                        user_id="concurrent-user",
                        title=name,
                        storage_path=path,
                        source_bytes=8,
                    )
                )
                session.commit()
            except QuotaExceededError:
                byte_outcomes.append("blocked")
            else:
                byte_outcomes.append("ok")

    writers = [threading.Thread(target=write_file, args=(f"{index}.txt",)) for index in range(2)]
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join(20)
    assert byte_outcomes.count("ok") == 1
    with Session(engine) as session:
        assert used_source_bytes(session, storage.id) <= 10


def test_downgrade_drops_quota_columns_and_keeps_rows(tmp_path: Path) -> None:
    database = tmp_path / "quota-down.db"
    env = os.environ.copy()
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), previous]) if previous else str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["DATABASE_URL"] = "sqlite:///" + database.resolve().as_posix()
    env["QUOTA_DB_PATH"] = str(database.resolve())
    env["ENV"] = "development"
    env["DB_ECHO"] = "false"
    env["INITIAL_ADMIN_USERNAME"] = ""
    env["INITIAL_ADMIN_PASSWORD"] = ""
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "quota_downgrade_probe.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "DOWNGRADE_OK" in result.stdout
