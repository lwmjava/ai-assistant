"""Independent scheduler contracts using real Engine persistence and synthetic Bridge."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlmodel import Session, SQLModel, create_engine, select

import app.models.base as base_mod
import app.workflow.engine as engine_mod
import app.workflow.scheduler as scheduler
from app.models.user import Tenant, User
from app.models.workflow import Workflow, WorkflowExecution


@pytest.fixture
def scheduled(tmp_path, monkeypatch):
    __import__("croniter")  # Required acceptance dependency; absence must fail, not skip.
    database = create_engine(f"sqlite:///{tmp_path / 'schedule.db'}")
    SQLModel.metadata.create_all(database)
    clock = [datetime(2026, 10, 20, 12, 0, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    calls = []

    async def execute(_self, session, owner, prompt):
        calls.append((owner.id, owner.tenant_id, prompt))
        if prompt == "fail":
            raise RuntimeError("synthetic bridge failure")
        return "scheduled result"

    monkeypatch.setattr(scheduler, "engine", database)
    monkeypatch.setattr(scheduler, "datetime", Clock)
    monkeypatch.setattr(engine_mod, "datetime", Clock)
    monkeypatch.setattr(base_mod, "datetime", Clock)
    monkeypatch.setattr(engine_mod.WorkflowBridge, "execute", execute)
    monkeypatch.setattr(scheduler, "_last_run", {})
    with Session(database) as session:
        session.add(Tenant(id="tenant", name="schedule tenant"))
        session.add(User(id="owner", tenant_id="tenant", username="schedule owner", hashed_password=""))
        session.commit()

    def add(**changes):
        fields = dict(tenant_id="tenant", owner_id="owner", name="scheduled", cron_expr="* * * * *",
                      prompt_template="scheduled input", timezone="UTC", created_at=clock[0] - timedelta(minutes=1))
        fields.update(changes)
        workflow = Workflow(**fields)
        with Session(database) as session:
            session.add(workflow)
            session.commit()
            session.refresh(workflow)
            session.expunge(workflow)
        return workflow

    def rows():
        with Session(database) as session:
            return list(session.exec(select(WorkflowExecution)).all())

    yield database, clock, calls, add, rows
    database.dispose()


@pytest.mark.asyncio
async def test_due_tick_persists_real_cron_execution(scheduled):
    _, clock, calls, add, rows = scheduled
    workflow = add()
    await scheduler._tick()
    records = rows()
    assert len(records) == 1, "at the scheduled boundary the real Engine must persist an execution"
    record = records[0]
    assert (record.workflow_id, record.tenant_id, record.triggered_by, record.status) == (
        workflow.id, "tenant", "cron", "success")
    assert (record.input, record.output) == ("scheduled input", "scheduled result")
    assert record.started_at is not None and record.finished_at is not None
    assert calls == [("owner", "tenant", "scheduled input")]
    await scheduler._tick()
    assert len(rows()) == 1
    clock[0] += timedelta(minutes=1)
    await scheduler._tick()
    assert len(rows()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running", "failed", "success"])
async def test_every_cron_attempt_consumes_slot_after_memory_reset(scheduled, status):
    database, clock, calls, add, rows = scheduled
    workflow = add()
    with Session(database) as session:
        session.add(WorkflowExecution(workflow_id=workflow.id, tenant_id="tenant", triggered_by="cron",
                                      status=status, created_at=clock[0]))
        session.commit()
    scheduler._last_run.clear()
    await scheduler._tick()
    assert len(rows()) == 1 and calls == []
    clock[0] += timedelta(minutes=1)
    await scheduler._tick()
    assert len(rows()) == 2 and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source,tenant", [("manual", "tenant"), ("cron", "foreign")])
async def test_manual_and_other_tenant_records_do_not_consume_slot(scheduled, source, tenant):
    database, clock, calls, add, rows = scheduled
    workflow = add()
    with Session(database) as session:
        session.add(WorkflowExecution(workflow_id=workflow.id, tenant_id=tenant, triggered_by=source,
                                      status="success", created_at=clock[0], finished_at=clock[0]))
        session.commit()
    await scheduler._tick()
    assert len(rows()) == 2 and len(calls) == 1


@pytest.mark.asyncio
async def test_max_created_at_not_started_or_finished_is_recovery_watermark(scheduled):
    database, clock, calls, add, rows = scheduled
    workflow = add(created_at=clock[0] - timedelta(hours=1))
    with Session(database) as session:
        session.add(WorkflowExecution(workflow_id=workflow.id, tenant_id="tenant", triggered_by="cron",
                                      status="success", created_at=clock[0] - timedelta(minutes=2),
                                      started_at=clock[0] + timedelta(minutes=20),
                                      finished_at=clock[0] + timedelta(minutes=30)))
        session.add(WorkflowExecution(workflow_id=workflow.id, tenant_id="tenant", triggered_by="cron",
                                      status="failed", created_at=clock[0]))
        session.commit()
    await scheduler._tick()
    assert len(rows()) == 2 and calls == []
    clock[0] += timedelta(minutes=1)
    await scheduler._tick()
    assert len(rows()) == 3 and len(calls) == 1


@pytest.mark.asyncio
async def test_run_latest_and_clock_rollback_do_not_replay_history(scheduled):
    _, clock, calls, add, rows = scheduled
    add(created_at=clock[0] - timedelta(days=7))
    await scheduler._tick()
    await scheduler._tick()
    assert len(rows()) == 1
    clock[0] -= timedelta(minutes=1)
    await scheduler._tick()
    assert len(rows()) == 1
    clock[0] += timedelta(minutes=3)
    await scheduler._tick()
    assert len(rows()) == 2 and len(calls) == 2
    scheduler._last_run.clear()
    await scheduler._tick()
    assert len(rows()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [timedelta(microseconds=1), timedelta(minutes=1)])
async def test_created_at_is_a_strict_lower_bound(scheduled, offset):
    _, clock, calls, add, rows = scheduled
    add(created_at=clock[0] + offset)
    await scheduler._tick()
    assert rows() == [] and calls == []


@pytest.mark.asyncio
async def test_created_at_equal_due_slot_can_execute(scheduled):
    _, clock, calls, add, rows = scheduled
    add(created_at=clock[0])
    await scheduler._tick()
    assert len(rows()) == 1 and len(calls) == 1


@pytest.mark.asyncio
async def test_fraction_before_boundary_does_not_execute_future_slot(scheduled):
    _, clock, calls, add, rows = scheduled
    add(created_at=clock[0] - timedelta(seconds=30))
    clock[0] -= timedelta(microseconds=1)
    await scheduler._tick()
    assert rows() == [] and calls == []
    clock[0] += timedelta(microseconds=1)
    await scheduler._tick()
    assert len(rows()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"enabled": False}, {"suspended_owner": True}])
async def test_inactive_workflows_are_not_scheduled(scheduled, changes):
    _, _, calls, add, rows = scheduled
    add(**changes)
    await scheduler._tick()
    assert rows() == [] and calls == []


@pytest.mark.asyncio
async def test_failed_bridge_and_invalid_schedules_do_not_block_other_workflows(scheduled):
    _, _, calls, add, rows = scheduled
    failed = add(prompt_template="fail")
    add(cron_expr="invalid")
    add(timezone="No/SuchZone")
    good = add()
    await scheduler._tick()
    records = {record.workflow_id: record for record in rows()}
    assert records[failed.id].status == "failed"
    assert records[good.id].status == "success"
    assert len(records) == 2 and len(calls) == 2
    await scheduler._tick()
    assert len(rows()) == 2 and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["disabled", "deleted", "suspended"])
async def test_trigger_rechecks_scanned_workflow_snapshot(scheduled, mutation):
    database, _, calls, add, rows = scheduled
    snapshot = add()
    with Session(database) as session:
        fresh = session.get(Workflow, snapshot.id)
        if mutation == "deleted":
            session.delete(fresh)
        elif mutation == "disabled":
            fresh.enabled = False
            session.add(fresh)
        else:
            fresh.suspended_owner = True
            session.add(fresh)
        session.commit()
    await scheduler._trigger(snapshot)
    assert rows() == [] and calls == []
    if mutation == "deleted":
        with Session(database) as session:
            assert session.get(Workflow, snapshot.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["disabled", "deleted", "moved"])
async def test_owner_revocation_keeps_existing_engine_boundary(scheduled, mutation):
    database, _, calls, add, rows = scheduled
    workflow = add()
    with Session(database) as session:
        owner = session.get(User, "owner")
        if mutation == "deleted":
            session.delete(owner)
        elif mutation == "disabled":
            owner.is_active = False
            session.add(owner)
        else:
            session.add(Tenant(id="new-tenant", name="new tenant"))
            owner.tenant_id = "new-tenant"
            session.add(owner)
        session.commit()
    await scheduler._tick()
    assert calls == []
    with Session(database) as session:
        fresh = session.get(Workflow, workflow.id)
        assert not fresh.enabled and fresh.suspended_owner
    assert len(rows()) == (0 if mutation == "deleted" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("zone,cron", [("UTC", "0 12 * * *"), ("Asia/Shanghai", "0 20 * * *"),
                                     ("America/New_York", "0 8 * * *")])
async def test_timezone_equivalent_absolute_boundary(scheduled, zone, cron):
    _, _, calls, add, rows = scheduled
    add(timezone=zone, cron_expr=cron)
    await scheduler._tick()
    assert len(rows()) == 1 and len(calls) == 1


@pytest.mark.asyncio
async def test_missing_cron_dependency_fails_closed(scheduled, monkeypatch):
    _, _, calls, add, rows = scheduled
    add()

    def missing():
        raise engine_mod.CroniterNotAvailableError("synthetic unavailable dependency")

    monkeypatch.setattr(engine_mod, "_require_croniter", missing)
    await scheduler._tick()
    assert rows() == [] and calls == []
    assert not scheduler._is_scheduler_runnable()


@pytest.mark.asyncio
async def test_dst_spring_nonexistent_time_preserves_croniter_candidate(scheduled):
    _, clock, calls, add, rows = scheduled
    clock[0] = datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
    add(timezone="America/New_York", cron_expr="30 2 * * *",
        created_at=clock[0] - timedelta(minutes=1))
    await scheduler._tick()
    assert len(rows()) == 1 and len(calls) == 1
    assert scheduler._last_run[next(iter(scheduler._last_run))] == clock[0]


@pytest.mark.asyncio
async def test_dst_fall_same_local_time_is_not_replayed_as_second_slot(scheduled):
    _, clock, calls, add, rows = scheduled
    clock[0] = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    add(timezone="America/New_York", cron_expr="30 1 * * *",
        created_at=clock[0] - timedelta(minutes=1))
    await scheduler._tick()
    assert len(rows()) == 1
    clock[0] += timedelta(hours=1)
    await scheduler._tick()
    assert len(rows()) == 1 and len(calls) == 1


@pytest.mark.asyncio
async def test_database_attempt_updates_watermark_even_with_older_memory(scheduled):
    database, clock, calls, add, rows = scheduled
    workflow = add(created_at=clock[0] - timedelta(hours=1))
    scheduler._last_run[workflow.id] = clock[0] - timedelta(minutes=1)
    with Session(database) as session:
        session.add(WorkflowExecution(workflow_id=workflow.id, tenant_id="tenant", triggered_by="cron",
                                      status="running", created_at=clock[0]))
        session.commit()
    await scheduler._tick()
    assert len(rows()) == 1 and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"cron_expr": "0 13 * * *"}, {"timezone": "Asia/Shanghai"}])
async def test_schedule_changed_after_scan_does_not_execute_stale_due(scheduled, monkeypatch, changes):
    database, _, calls, add, rows = scheduled
    workflow = add(cron_expr="0 12 * * *")
    original_watermark = scheduler._most_recent_execution_time

    def mutate_after_scan(workflow_id, tenant_id):
        with Session(database) as session:
            current = session.get(Workflow, workflow.id)
            for field, value in changes.items():
                setattr(current, field, value)
            session.add(current)
            session.commit()
        return original_watermark(workflow_id, tenant_id)

    monkeypatch.setattr(scheduler, "_most_recent_execution_time", mutate_after_scan)
    await scheduler._tick()
    assert rows() == [], "a schedule edited after scan must not execute the stale due slot"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["inactive", "missing"])
async def test_tenant_revocation_prevents_bridge_and_execution(scheduled, mutation):
    database, _, calls, add, rows = scheduled
    add()
    with Session(database) as session:
        if mutation == "missing":
            session.execute(delete(Tenant).where(Tenant.id == "tenant"))
        else:
            tenant = session.get(Tenant, "tenant")
            tenant.is_active = False
            session.add(tenant)
        session.commit()
    with Session(database) as session:
        owner = session.get(User, "owner")
        assert owner is not None and owner.is_active and owner.tenant_id == "tenant"
    await scheduler._tick()
    assert rows() == [], "inactive or missing tenants must not create a cron execution"
    assert calls == []
