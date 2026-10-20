"""Scheduler retries rejected periods and passes their exact UTC identity."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import app.models.base as base_mod
import app.workflow.engine as engine_mod
import app.workflow.scheduler as scheduler
from app.core.config import settings
from app.models.user import Tenant, User
from app.models.workflow import Workflow, WorkflowExecution


@pytest.fixture
def scheduled_admission(tmp_path, monkeypatch):
    database = create_engine(f"sqlite:///{tmp_path / 'admission.db'}")
    SQLModel.metadata.create_all(database)
    clock = [datetime(2026, 10, 20, 12, 0, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    calls = []

    async def execute(_self, session, owner, prompt):
        calls.append(prompt)
        return "result"

    monkeypatch.setattr(scheduler, "engine", database)
    monkeypatch.setattr(scheduler, "datetime", Clock)
    monkeypatch.setattr(engine_mod, "datetime", Clock)
    monkeypatch.setattr(base_mod, "datetime", Clock)
    monkeypatch.setattr(engine_mod.WorkflowBridge, "execute", execute)
    monkeypatch.setattr(scheduler, "_last_run", {})
    with Session(database) as session:
        session.add(Tenant(id="tenant", name="tenant"))
        session.add(User(id="owner", tenant_id="tenant", username="owner", hashed_password=""))
        workflow = Workflow(id="scheduled", tenant_id="tenant", owner_id="owner", name="scheduled",
                            cron_expr="* * * * *", prompt_template="prompt", timezone="UTC",
                            created_at=clock[0] - timedelta(minutes=1))
        session.add(workflow)
        session.commit()
    yield database, clock, calls
    database.dispose()


@pytest.mark.asyncio
async def test_rejected_tick_does_not_consume_scheduler_watermark(scheduled_admission, monkeypatch):
    async def reject(wf):
        raise RuntimeError("synthetic admission rejection")

    original = scheduler._trigger
    monkeypatch.setattr(scheduler, "_trigger", reject)
    await scheduler._tick()
    assert "scheduled" not in scheduler._last_run
    monkeypatch.setattr(scheduler, "_trigger", original)
    await scheduler._tick()
    assert scheduled_admission[2] == ["prompt"]


@pytest.mark.asyncio
async def test_tick_passes_exact_scan_due(scheduled_admission, monkeypatch):
    _, clock, _ = scheduled_admission
    expected = clock[0]
    received = []

    async def execute(self, session, wf, owner, **kwargs):
        received.append(kwargs.get("scheduled_due"))

    monkeypatch.setattr(engine_mod.WorkflowEngine, "execute", execute)
    await scheduler._tick()
    assert received == [expected]


@pytest.mark.asyncio
async def test_capacity_rejection_retries_same_due_then_next_period(scheduled_admission, monkeypatch):
    from app.workflow.admission import acquire

    database, clock, calls = scheduled_admission
    monkeypatch.setattr(settings, "WORKFLOW_MAX_CONCURRENCY", 1)
    lease = acquire(database, "tenant", "other")
    try:
        await scheduler._tick()
        assert calls == []
        assert "scheduled" not in scheduler._last_run
        with Session(database) as session:
            assert session.exec(select(WorkflowExecution)).all() == []
    finally:
        lease.release()
    await scheduler._tick()
    await scheduler._tick()
    assert calls == ["prompt"]
    # Admission also protects the due if process-local scheduler watermarks are lost.
    scheduler._last_run.clear()
    monkeypatch.setattr(scheduler, "_most_recent_execution_time", lambda *args: None)
    await scheduler._tick()
    assert calls == ["prompt"]
    clock[0] += timedelta(minutes=1)
    await scheduler._tick()
    assert calls == ["prompt", "prompt"]
    with Session(database) as session:
        assert len(session.exec(select(WorkflowExecution)).all()) == 2
