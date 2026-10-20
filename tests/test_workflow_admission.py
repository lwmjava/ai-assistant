"""workflow-concurrency-idempotency-v1 Smoke/Adversarial，隔离持久化入口。"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution
from app.workflow.engine import WorkflowEngine


@pytest.fixture
def jobs(persist_identity):
    owner = User(id="admission-owner", tenant_id="admission-tenant", username="admission-owner",
                 hashed_password="", role=Role.MEMBER.value, is_active=True)
    persist_identity(owner)
    rows = [Workflow(id=f"admission-{i}", owner_id=owner.id, tenant_id=owner.tenant_id,
                     name=f"job-{i}", cron_expr="* * * * *", prompt_template="hello", timeout_seconds=10)
            for i in range(3)]
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()
        for row in rows:
            session.refresh(row)
            session.expunge(row)
    from app.workflow.admission import forget_workflow
    for row in rows:
        forget_workflow(engine, row.id, row.tenant_id)
    yield rows, owner
    for row in rows:
        forget_workflow(engine, row.id, row.tenant_id)
    with Session(engine) as session:
        for row in session.exec(select(WorkflowExecution).where(
                col(WorkflowExecution.tenant_id) == owner.tenant_id)).all():
            session.delete(row)
        for row in rows:
            persisted = session.get(Workflow, row.id)
            if persisted:
                session.delete(persisted)
        session.commit()


class Gate:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def execute(self, session, owner, prompt):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return "ok"


async def run(wf, owner, bridge, **kwargs):
    with Session(engine) as session:
        return await WorkflowEngine(bridge).execute(session, wf, owner, **kwargs)


def history(wf):
    with Session(engine) as fresh:
        return list(fresh.exec(select(WorkflowExecution).where(
            col(WorkflowExecution.workflow_id) == wf.id)).all())


@pytest.mark.asyncio
async def test_active_duplicate_has_one_history_and_bridge(jobs):
    rows, owner = jobs
    gate = Gate()
    first = asyncio.create_task(run(rows[0], owner, gate))
    await gate.entered.wait()
    duplicates = [asyncio.create_task(run(rows[0], owner, gate, prompt_override="different"))
                  for _ in range(6)]
    await asyncio.sleep(0.03)
    try:
        assert len(history(rows[0])) == 1
        assert gate.calls == 1
    finally:
        gate.release.set()
        results = await asyncio.gather(first, *duplicates, return_exceptions=True)
    assert results[0].status == "success"
    assert all(getattr(x, "code", None) == "WORKFLOW_ALREADY_RUNNING" for x in results[1:])
    await run(rows[0], owner, gate)
    assert len(history(rows[0])) == 2


@pytest.mark.asyncio
async def test_global_capacity_shared_across_workflows(jobs, monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_MAX_CONCURRENCY", 1, raising=False)
    rows, owner = jobs
    gate = Gate()
    first = asyncio.create_task(run(rows[0], owner, gate))
    await gate.entered.wait()
    second = asyncio.create_task(run(rows[1], owner, gate))
    await asyncio.sleep(0.03)
    try:
        assert history(rows[1]) == []
        assert gate.calls == 1
    finally:
        gate.release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
    assert getattr(results[1], "code", None) == "WORKFLOW_CAPACITY_EXCEEDED"
    await run(rows[1], owner, gate)
    assert len(history(rows[1])) == 1


@pytest.mark.asyncio
async def test_cron_terminal_replay_old_slot_and_next_slot(jobs):
    rows, owner = jobs
    gate = Gate()
    gate.release.set()
    due = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
    await run(rows[0], owner, gate, triggered_by="cron", scheduled_due=due)
    from app.workflow.admission import WorkflowAdmissionError
    for repeated in (due, due - timedelta(minutes=1)):
        with pytest.raises(WorkflowAdmissionError, match="WORKFLOW_PERIOD_CONSUMED"):
            await run(rows[0], owner, gate, triggered_by="cron", scheduled_due=repeated)
    await run(rows[0], owner, gate, triggered_by="cron", scheduled_due=due + timedelta(minutes=1))
    assert gate.calls == 2 and len(history(rows[0])) == 2


@pytest.mark.asyncio
async def test_first_commit_failure_releases_unconsumed_slot(jobs, monkeypatch):
    rows, owner = jobs
    gate = Gate()
    gate.release.set()
    due = datetime(2026, 10, 20, 2, 0, tzinfo=UTC)
    with Session(engine) as session:
        with monkeypatch.context() as patch:
            patch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit unavailable")))
            with pytest.raises(RuntimeError, match="commit unavailable"):
                await WorkflowEngine(gate).execute(session, rows[1], owner, "cron", scheduled_due=due)
    assert history(rows[1]) == [] and gate.calls == 0
    await run(rows[1], owner, gate, triggered_by="cron", scheduled_due=due)
    assert len(history(rows[1])) == 1


@pytest.mark.asyncio
async def test_terminal_commit_failure_keeps_consumed_slot(jobs, monkeypatch):
    from app.workflow.admission import WorkflowAdmissionError
    rows, owner = jobs
    gate = Gate()
    gate.release.set()
    due = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
    with Session(engine) as session:
        original = session.commit
        commits = 0

        def commit():
            nonlocal commits
            commits += 1
            if commits == 2:
                raise RuntimeError("terminal unavailable")
            original()

        monkeypatch.setattr(session, "commit", commit)
        with pytest.raises(RuntimeError, match="terminal unavailable"):
            await WorkflowEngine(gate).execute(session, rows[2], owner, "cron", scheduled_due=due)
    with pytest.raises(WorkflowAdmissionError, match="WORKFLOW_PERIOD_CONSUMED"):
        await run(rows[2], owner, gate, triggered_by="cron", scheduled_due=due)
    assert gate.calls == 1 and len(history(rows[2])) == 1
    assert history(rows[2])[0].status == "running"
    await run(rows[2], owner, gate)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["create_task", "call_later", "refresh"])
async def test_setup_failure_releases_capacity_and_keeps_slot(jobs, monkeypatch, fault):
    from app.workflow.admission import WorkflowAdmissionError, is_active
    from app.workflow.control import database_key
    rows, owner = jobs
    gate = Gate()
    gate.release.set()
    due = datetime(2026, 10, 20, 4, 0, tzinfo=UTC)
    with Session(engine) as session:
        with monkeypatch.context() as patch:
            target = (asyncio if fault == "create_task"
                      else asyncio.get_running_loop() if fault == "call_later" else session)
            def fail(*args, **kwargs):
                raise RuntimeError("setup unavailable")

            patch.setattr(target, fault, fail)
            with pytest.raises(RuntimeError, match="setup unavailable"):
                await WorkflowEngine(gate).execute(session, rows[0], owner, "cron", scheduled_due=due)
    assert not is_active(rows[0].id, rows[0].tenant_id, database_key(engine))
    with pytest.raises(WorkflowAdmissionError, match="WORKFLOW_PERIOD_CONSUMED"):
        await run(rows[0], owner, gate, triggered_by="cron", scheduled_due=due)
    assert gate.calls == 0
    await run(rows[1], owner, gate)


@pytest.mark.asyncio
async def test_cancel_cleanup_and_webhook_hold_capacity(jobs, monkeypatch):
    from app.workflow.admission import WorkflowAdmissionError
    monkeypatch.setattr(settings, "WORKFLOW_MAX_CONCURRENCY", 1)
    rows, owner = jobs
    cleaning, cleaned = asyncio.Event(), asyncio.Event()
    gate = Gate()

    class CleanupGate(Gate):
        async def execute(self, session, owner, prompt):
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await cleaned.wait()

    bridge = CleanupGate()
    first = asyncio.create_task(run(rows[0], owner, bridge))
    await bridge.entered.wait()
    first.cancel()
    await cleaning.wait()
    try:
        with pytest.raises(WorkflowAdmissionError, match="WORKFLOW_CAPACITY_EXCEEDED"):
            await run(rows[1], owner, gate)
    finally:
        cleaned.set()
        with pytest.raises(asyncio.CancelledError):
            await first
    entered, released = asyncio.Event(), asyncio.Event()

    async def webhook(self, wf, execution):
        entered.set()
        await released.wait()

    monkeypatch.setattr(WorkflowEngine, "_fire_webhook", webhook)
    rows[0].webhook_url = "https://example.invalid/hook"
    with Session(engine) as session:
        row = session.get(Workflow, rows[0].id)
        row.webhook_url = rows[0].webhook_url
        session.add(row)
        session.commit()
    gate.release.set()
    second = asyncio.create_task(run(rows[0], owner, gate))
    await entered.wait()
    try:
        with pytest.raises(WorkflowAdmissionError, match="WORKFLOW_CAPACITY_EXCEEDED"):
            await run(rows[1], owner, gate)
    finally:
        released.set()
        await second
    await run(rows[1], owner, gate)


@pytest.mark.asyncio
async def test_capacity_can_reach_limit(jobs, monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_MAX_CONCURRENCY", 2)
    rows, owner = jobs
    gate = Gate()
    first = asyncio.create_task(run(rows[0], owner, gate))
    second = asyncio.create_task(run(rows[1], owner, gate))
    await asyncio.sleep(0.03)
    try:
        assert gate.calls == 2
        from app.workflow.admission import WorkflowAdmissionError
        with pytest.raises(WorkflowAdmissionError, match="WORKFLOW_CAPACITY_EXCEEDED"):
            await run(rows[2], owner, gate)
    finally:
        gate.release.set()
        await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_slot_forget_and_manual_does_not_consume(jobs):
    from app.workflow.admission import forget_workflow
    rows, owner = jobs
    gate = Gate()
    gate.release.set()
    due = datetime(2026, 10, 20, 5, 0, tzinfo=UTC)
    await run(rows[1], owner, gate)
    await run(rows[1], owner, gate, triggered_by="cron", scheduled_due=due)
    forget_workflow(engine, rows[1].id, rows[1].tenant_id)
    await run(rows[1], owner, gate, triggered_by="cron", scheduled_due=due)
    assert gate.calls == 3
