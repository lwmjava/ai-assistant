"""Scheduled execution through the real engine, bridge and ChatService with isolated SQLite."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, col, create_engine, delete, select

from app.api.deps import get_current_user
from app.core.config import settings
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.conversation import Conversation, Message
from app.models.user import Tenant, User
from app.models.workflow import Workflow, WorkflowExecution
from app.workflow import scheduler


@pytest.fixture
def scheduled_app(tmp_path, monkeypatch):
    database = create_engine(
        f"sqlite:///{tmp_path / 'scheduled-service.db'}", connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(database)
    for name, value in {
        "WORKFLOW_ENABLED": True,
        "RAG_ENABLED": False,
        "EVOLUTION_ENABLED": False,
        "LLM_RESPONSE_CACHE_ENABLED": False,
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(scheduler, "engine", database)
    monkeypatch.setattr("app.core.database.engine", database)
    monkeypatch.setattr("app.audit.logger.engine", database)
    monkeypatch.setattr(scheduler, "_last_run", {})
    actor = User(id=uuid4().hex, tenant_id=uuid4().hex, username=uuid4().hex, hashed_password="")
    with Session(database) as session:
        session.add(Tenant(id=actor.tenant_id, name=actor.tenant_id))
        session.add(actor)
        session.commit()
        session.refresh(actor)
        session.expunge(actor)
    clock = SimpleNamespace(now=datetime(2026, 10, 20, 12, 0, 59, 999999, tzinfo=UTC))

    class ControlledDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now.astimezone(tz) if tz is not None else clock.now.replace(tzinfo=None)

    monkeypatch.setattr(scheduler, "datetime", ControlledDateTime)
    monkeypatch.setattr("app.workflow.engine.datetime", ControlledDateTime)
    monkeypatch.setattr("app.models.base.datetime", ControlledDateTime)
    model = MockLLMProvider()
    chat = model.chat
    clock.model_calls = []

    async def counted_chat(messages, options=None):
        clock.model_calls.append(messages)
        return await chat(messages, options)

    monkeypatch.setattr(model, "chat", counted_chat)
    set_llm_provider_override(model)
    app.dependency_overrides[get_current_user] = lambda: actor
    try:
        yield database, actor, clock
    finally:
        awaitable = scheduler._task
        assert awaitable is None or awaitable.done(), "Integration test must stop its scheduler."
        set_llm_provider_override(None)
        app.dependency_overrides.pop(get_current_user, None)
        database.dispose()


def create_job(database, actor, clock):
    workflow = Workflow(
        tenant_id=actor.tenant_id,
        owner_id=actor.id,
        name="Scheduled report",
        cron_expr="* * * * *",
        timezone="Asia/Shanghai",
        prompt_template="hello from the scheduled report",
        created_at=clock.now - timedelta(seconds=20),
    )
    with Session(database) as session:
        session.add(workflow)
        session.commit()
        session.refresh(workflow)
        return workflow.id


def executions(database, workflow_id):
    with Session(database) as session:
        return session.exec(
            select(WorkflowExecution).where(col(WorkflowExecution.workflow_id) == workflow_id)
        ).all()


@pytest.mark.parametrize("orchestration", ["self", "langgraph"])
async def test_due_tick_persists_actual_chat_and_history(scheduled_app, monkeypatch, orchestration):
    database, actor, clock = scheduled_app
    monkeypatch.setattr(settings, "AGENT_ORCHESTRATION", orchestration)
    workflow_id = create_job(database, actor, clock)
    await scheduler._tick()
    assert executions(database, workflow_id) == []

    clock.now += timedelta(microseconds=1)
    await scheduler._tick()
    rows = executions(database, workflow_id)
    assert len(rows) == 1, "Crossing the scheduled minute must persist a cron execution."
    execution = rows[0]
    assert execution.triggered_by == "cron" and execution.status == "success"
    assert execution.tenant_id == actor.tenant_id
    assert execution.input == "hello from the scheduled report"
    assert execution.output and execution.duration_ms is not None
    assert execution.started_at is not None and execution.finished_at is not None
    with Session(database) as fresh:
        conversations = fresh.exec(select(Conversation)).all()
        messages = fresh.exec(select(Message)).all()
        assert len(conversations) == 1
        assert conversations[0].user_id == actor.id and conversations[0].tenant_id == actor.tenant_id
        assert any(message.role == "user" and message.content == execution.input for message in messages)
        assert any(message.role == "assistant" and message.content == execution.output for message in messages)
    # No TestClient lifespan: this query cannot secretly start another scheduler.
    client = TestClient(app)
    try:
        response = client.get(f"/api/v1/workflows/{workflow_id}/executions")
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["id"] == execution.id
        assert response.json()[0]["output"] == execution.output
    finally:
        client.close()
    await scheduler._tick()
    assert len(executions(database, workflow_id)) == 1
    clock.now += timedelta(minutes=1)
    await scheduler._tick()
    assert len(executions(database, workflow_id)) == 2


async def test_running_loop_crosses_due_time_without_manual_trigger(scheduled_app, monkeypatch):
    database, actor, clock = scheduled_app
    workflow_id = create_job(database, actor, clock)
    sleeps = []

    async def advance_time(delay):
        sleeps.append(delay)
        if len(sleeps) == 1:
            clock.now += timedelta(microseconds=1)
        else:
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduler, "asyncio", SimpleNamespace(
        sleep=advance_time, create_task=asyncio.create_task, CancelledError=asyncio.CancelledError,
    ))
    try:
        await scheduler.start_scheduler()
        assert scheduler._task is not None
        await scheduler._task
    finally:
        await scheduler.stop_scheduler()
    rows = executions(database, workflow_id)
    assert len(rows) == 1, "The actual started loop must reach a scheduled execution."
    assert rows[0].triggered_by == "cron" and rows[0].status == "success"
    assert rows[0].output and len(sleeps) == 2


async def test_manual_history_does_not_suppress_actual_cron(scheduled_app):
    database, actor, clock = scheduled_app
    workflow_id = create_job(database, actor, clock)
    client = TestClient(app)
    try:
        response = client.post(f"/api/v1/workflows/{workflow_id}/run")
        assert response.status_code == 201 and response.json()["triggered_by"] == "manual"
        clock.now += timedelta(microseconds=1)
        await scheduler._tick()
        rows = executions(database, workflow_id)
        assert len(rows) == 2
        assert sorted(row.triggered_by for row in rows) == ["cron", "manual"]
        history = client.get(f"/api/v1/workflows/{workflow_id}/executions")
        assert history.status_code == 200 and len(history.json()) == 2
    finally:
        client.close()


@pytest.mark.parametrize("mutation", ["inactive", "missing"])
async def test_invalid_tenant_starts_no_actual_chat(scheduled_app, mutation):
    database, actor, clock = scheduled_app
    workflow_id = create_job(database, actor, clock)
    with Session(database) as session:
        if mutation == "missing":
            session.execute(delete(Tenant).where(col(Tenant.id) == actor.tenant_id))
        else:
            tenant = session.get(Tenant, actor.tenant_id)
            tenant.is_active = False
            session.add(tenant)
        session.commit()
    clock.now += timedelta(microseconds=1)
    await scheduler._tick()
    assert executions(database, workflow_id) == []
    assert clock.model_calls == []
    with Session(database) as fresh:
        assert fresh.exec(select(Conversation)).all() == []
        assert fresh.exec(select(Message)).all() == []
        workflow = fresh.get(Workflow, workflow_id)
        assert workflow.enabled and not workflow.suspended_owner
