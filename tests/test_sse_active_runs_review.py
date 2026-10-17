"""运行所有权、回放边界与独立会话释放的反例验证。"""

import asyncio
import json
import logging
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.agents.pipeline import AgentEvent
from app.api.deps import get_current_user, get_db
from app.api.routes import chat as chat_routes
from app.core.security import Role
from app.main import app
from app.models.conversation import Message
from app.models.user import Tenant, User
from app.services import active_runs as module
from app.services.chat_service import ChatService


@pytest.fixture
async def run_context():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        tenant = Tenant(id="review-tenant", name="review-tenant")
        user = User(id="review-owner", tenant_id=tenant.id, username="review-owner", hashed_password="")
        session.add_all([tenant, user])
        session.commit()
        session.refresh(user)
        session.expunge(user)
    manager = module.ActiveRunManager()
    yield SimpleNamespace(engine=engine, user=user, manager=manager)
    await manager.shutdown()
    engine.dispose()


def token(text):
    return SimpleNamespace(type="token", data=text)


async def finish(run):
    await asyncio.wait_for(asyncio.shield(run.task), timeout=2)


async def test_producer_failure_logs_exception_type_without_message(run_context, caplog):
    context = run_context
    secret = "sk-secret-token-should-not-be-logged"
    private = "用户原文不应出现在日志"

    async def producer(_session, _user):
        raise RuntimeError(f"{secret} {private}")
        yield token("unused")

    with caplog.at_level(logging.ERROR, logger="app.services.active_runs"):
        run = context.manager.create(context.user, context.engine, producer)
        await finish(run)

    assert run.status == "failed"
    error = next(event for _number, event, _size in run.events if event["event"] == "error")
    assert json.loads(error["data"])["message"] == "运行失败，请稍后重试"
    diagnostic = [record for record in caplog.records if record.name == "app.services.active_runs"]
    assert len(diagnostic) == 1
    message = diagnostic[0].getMessage()
    assert message == f"active_run_failed run_id={run.run_id} exception_type=RuntimeError"
    assert diagnostic[0].exc_info is None
    assert secret not in message
    assert private not in message


async def test_disconnect_and_replay_do_not_restart_or_cancel_producer(run_context):
    context = run_context
    release = asyncio.Event()
    calls = []

    async def producer(session, user):
        calls.append((id(session), user.id))
        yield token("first")
        await release.wait()
        yield token("second")

    run = context.manager.create(context.user, context.engine, producer)
    await asyncio.wait_for(run.ready.wait(), timeout=2)
    subscription = context.manager.subscribe(run, context.user, 0)
    first = await anext(subscription)
    await subscription.aclose()
    assert not run.task.done()
    with pytest.raises(module.RunConflictError):
        context.manager.create(context.user, context.engine, producer, run_id=run.run_id)
    release.set()
    await finish(run)
    after = context.manager.cursor(run, first["id"])
    replay = [event async for event in context.manager.subscribe(run, context.user, after)]
    assert len(calls) == 1
    assert run.status == "done"
    assert [event["event"] for event in replay].count("token") == 2
    assert replay[-1]["event"] == "run.finished"


async def test_cancel_after_message_commit_retains_binding_before_first_metadata(run_context, monkeypatch):
    context = run_context
    entered = asyncio.Event()
    service = ChatService()

    async def pending_memory(_conversation):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_build_memory", pending_memory)
    run = context.manager.create(
        context.user, context.engine,
        lambda session, user: service.chat_stream(session, user, "isolated cancellation before metadata"),
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    with Session(context.engine) as session:
        message = session.exec(select(Message).where(col(Message.role) == "user")).one()
        expected_id, expected_conversation = message.id, message.conversation_id
    await context.manager.cancel(run, context.user)
    assert run.status == "cancelled"
    assert run.message_id == expected_id
    assert run.conversation_id == expected_conversation


@pytest.mark.parametrize("revocation", ["version", "user_disabled", "tenant_disabled", "role", "tenant"])
async def test_revocation_denies_replay_and_stops_producer(run_context, monkeypatch, revocation):
    context = run_context
    monkeypatch.setattr(module, "HEARTBEAT_SECONDS", 0.01)
    closed = asyncio.Event()

    async def producer(_session, _user):
        try:
            yield token("before revocation")
            await asyncio.Event().wait()
        finally:
            closed.set()

    run = context.manager.create(context.user, context.engine, producer)
    await asyncio.wait_for(run.ready.wait(), timeout=2)
    with Session(context.engine) as session:
        user = session.get(User, context.user.id)
        if revocation == "version":
            user.token_version += 1
        elif revocation == "user_disabled":
            user.is_active = False
        elif revocation == "tenant_disabled":
            tenant = session.get(Tenant, user.tenant_id)
            tenant.is_active = False
            session.add(tenant)
        elif revocation == "role":
            user.role = Role.VIEWER.value
        else:
            tenant = Tenant(id="replacement-tenant", name="replacement-tenant")
            session.add(tenant)
            user.tenant_id = tenant.id
        session.add(user)
        session.commit()
    with pytest.raises(module.RunAccessError):
        context.manager.get(run.run_id, context.user)
    denied = context.manager.subscribe(run, context.user, 0)
    with pytest.raises(module.RunAccessError):
        await anext(denied)
    await finish(run)
    assert closed.is_set()
    assert run.status in {"cancelled", "failed"}


async def test_other_owner_cannot_query_subscribe_or_cancel(run_context):
    context = run_context

    async def producer(_session, _user):
        yield token("owned")
        await asyncio.Event().wait()

    run = context.manager.create(context.user, context.engine, producer)
    await asyncio.wait_for(run.ready.wait(), timeout=2)
    other = User(id="other", tenant_id=context.user.tenant_id, username="other", hashed_password="")
    for user in (other, context.user.model_copy(update={"tenant_id": "other-tenant"})):
        with pytest.raises(module.RunAccessError):
            context.manager.get(run.run_id, user)
        with pytest.raises(module.RunAccessError):
            await context.manager.cancel(run, user)
        with pytest.raises(module.RunAccessError):
            await anext(context.manager.subscribe(run, user, 0))
    assert not run.task.done()


async def test_other_owner_cannot_probe_existing_run_through_explicit_submission(run_context):
    context = run_context

    async def producer(_session, _user):
        yield token("owned")
        await asyncio.Event().wait()

    run = context.manager.create(context.user, context.engine, producer)
    await asyncio.wait_for(run.ready.wait(), timeout=2)
    with Session(context.engine) as session:
        other = User(id="other-owner", tenant_id=context.user.tenant_id,
                     username="other-owner", hashed_password="")
        session.add(other)
        session.commit()
        session.refresh(other)
        session.expunge(other)
    with pytest.raises(module.RunAccessError):
        context.manager.create(other, context.engine, producer, run_id=run.run_id)
    assert not run.task.done()


async def test_buffer_limits_reject_invalid_and_expired_cursors(run_context, monkeypatch):
    context = run_context
    monkeypatch.setattr(module, "MAX_EVENTS", 3)
    monkeypatch.setattr(module, "MAX_EVENT_BYTES", 500)
    run = module.ActiveRun("a" * 32, (context.user.id, context.user.tenant_id, context.user.role, 0), context.engine)
    for _ in range(10):
        context.manager.emit(run, "token", {"delta": "x" * 50})
    assert len(run.events) <= 3
    assert run.event_bytes <= 500
    assert run.event_bytes == sum(size for _, _, size in run.events)
    for cursor in (None, "invalid", "b" * 32 + ":1", run.run_id + ":-1", run.run_id + ":999"):
        with pytest.raises(module.RunConflictError):
            context.manager.cursor(run, cursor)
    sequence = run.sequence
    with pytest.raises(module.RunCapacityError):
        context.manager.emit(run, "token", {"delta": "x" * 501})
    assert run.sequence == sequence
    assert context.manager.cursor(run, f"{run.run_id}:{sequence}") == sequence


async def test_slow_subscription_cannot_block_producer_or_silently_skip_window(run_context, monkeypatch):
    context = run_context
    monkeypatch.setattr(module, "MAX_EVENTS", 3)
    release = asyncio.Event()

    async def producer(_session, _user):
        yield token("initial")
        await release.wait()
        for index in range(20):
            yield token(str(index))

    run = context.manager.create(context.user, context.engine, producer)
    await asyncio.wait_for(run.ready.wait(), timeout=2)
    slow = context.manager.subscribe(run, context.user, 0)
    await anext(slow)
    release.set()
    await finish(run)
    assert run.status == "done"
    assert len(run.events) == 3
    with pytest.raises(module.RunConflictError):
        async for _event in slow:
            pass


async def test_terminal_ttl_and_full_active_capacity_do_not_evict_running_tasks(run_context, monkeypatch):
    context = run_context
    monkeypatch.setattr(module, "MAX_RUNS", 1)

    async def producer(_session, _user):
        yield token("waiting")
        await asyncio.Event().wait()

    run = context.manager.create(context.user, context.engine, producer)
    await asyncio.wait_for(run.ready.wait(), timeout=2)
    with pytest.raises(module.RunCapacityError):
        context.manager.create(context.user, context.engine, producer)
    assert not run.task.done()
    await context.manager.cancel(run, context.user)
    run.finished_at = time.monotonic() - module.TERMINAL_TTL_SECONDS - 1
    with pytest.raises(module.RunAccessError):
        context.manager.get(run.run_id, context.user)
    assert not context.manager.runs


async def test_shutdown_closes_producer_and_its_independent_session(run_context, monkeypatch):
    context = run_context
    sessions = []
    closed = asyncio.Event()

    class TrackedSession(Session):
        def close(self):
            self.review_closed = True
            super().close()

    monkeypatch.setattr(module, "Session", TrackedSession)

    async def producer(session, _user):
        sessions.append(session)
        try:
            yield token("waiting")
            await asyncio.Event().wait()
        finally:
            closed.set()

    run = context.manager.create(context.user, context.engine, producer)
    await asyncio.wait_for(run.ready.wait(), timeout=2)
    await context.manager.shutdown()
    assert run.task.done()
    assert closed.is_set()
    assert len(sessions) == 1
    assert sessions[0].review_closed is True
    assert not context.manager.runs


@pytest.fixture
def wire_client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        tenant = Tenant(id="wire-tenant", name="wire-tenant")
        user = User(id="wire-owner", tenant_id=tenant.id, username="wire-owner", hashed_password="")
        session.add_all([tenant, user])
        session.commit()
        session.refresh(user)
        session.expunge(user)

    def request_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(chat_routes, "active_runs", module.ActiveRunManager())
    monkeypatch.setattr(module, "HEARTBEAT_SECONDS", 0.005)
    app.dependency_overrides[get_db] = request_session
    app.dependency_overrides[get_current_user] = lambda: user
    # 无 lifespan：只测真实 HTTP 路由/管理器的编码，不启动共享库后台任务。
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)
    engine.dispose()


def wire_payloads(response, name):
    assert response.status_code == 200
    result = []
    for block in response.text.replace("\r\n", "\n").split("\n\n"):
        values = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if values.get("event") == name:
            result.append(json.loads(values["data"]))
    assert result
    return result


def test_http_error_wire_contains_safe_code_and_retryability(wire_client, monkeypatch):
    async def producer(_session, _user, *_args, **_kwargs):
        yield AgentEvent("error", "private internal wire failure")

    monkeypatch.setattr(chat_routes._service, "chat_stream", producer)
    response = wire_client.post("/api/v1/chat/stream", json={"message": "schema check"})
    error = wire_payloads(response, "error")[0]
    assert error["run_id"]
    assert isinstance(error.get("code"), str) and error["code"]
    assert isinstance(error.get("retryable"), bool)
    assert "private internal wire failure" not in response.text


def test_http_rate_limit_wire_preserves_countdown_seconds(wire_client, monkeypatch):
    async def producer(_session, _user, *_args, **_kwargs):
        yield AgentEvent("rate_limit", '{"retry_after_seconds":7}')

    monkeypatch.setattr(chat_routes._service, "chat_stream", producer)
    response = wire_client.post("/api/v1/chat/stream", json={"message": "limit schema check"})
    error = wire_payloads(response, "error")[0]
    assert error["code"] == "rate_limited"
    assert error["retry_after_seconds"] == 7
    assert error["retryable"] is False


def test_http_heartbeat_wire_contains_timestamp(wire_client, monkeypatch):
    async def producer(_session, _user, *_args, **_kwargs):
        yield AgentEvent("token", "initial")
        await asyncio.sleep(0.03)

    monkeypatch.setattr(chat_routes._service, "chat_stream", producer)
    response = wire_client.post("/api/v1/chat/stream", json={"message": "heartbeat schema check"})
    for heartbeat in wire_payloads(response, "heartbeat"):
        assert heartbeat["run_id"]
        assert isinstance(heartbeat.get("ts"), str | int | float)
        assert heartbeat["ts"]
