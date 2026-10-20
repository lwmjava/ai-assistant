"""Independent E2 admission entry adversarial cases, version 1."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic, sleep

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from starlette.requests import Request

from app.api.routes import workflow as routes
from app.core.config import settings
from app.models.user import Tenant, User
from app.models.workflow import Workflow, WorkflowExecution
from app.workflow.admission import WorkflowAdmissionError
from app.workflow.control import database_key, has_active_workflow
from app.workflow.engine import WorkflowEngine


@pytest.fixture
def review_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_MAX_CONCURRENCY", 1)
    bind = create_engine(f"sqlite:///{tmp_path / 'admission-review.db'}",
                         connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(bind)
    with Session(bind) as db:
        db.add(Tenant(id="tenant", name="tenant"))
        db.add(User(id="owner", tenant_id="tenant", username="owner", hashed_password=""))
        for identity in ("first", "second"):
            db.add(Workflow(id=identity, tenant_id="tenant", owner_id="owner", name=identity,
                            cron_expr="* * * * *", prompt_template="synthetic"))
        db.commit()
    yield bind
    bind.dispose()


class Instant:
    calls = 0

    async def execute(self, session, owner, prompt):
        self.calls += 1
        return "synthetic result"


async def run(bind, identity, bridge, **kwargs):
    with Session(bind) as db:
        return await WorkflowEngine(bridge).execute(
            db, db.get(Workflow, identity), db.get(User, "owner"), **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_workflow", [True, False])
async def test_review_threads_share_workflow_and_capacity_admission(review_db, monkeypatch, same_workflow):
    bind = review_db
    if same_workflow:
        monkeypatch.setattr(settings, "WORKFLOW_MAX_CONCURRENCY", 4)
    entered, release = Event(), Event()

    class ThreadBridge:
        async def execute(self, session, owner, prompt):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            return "done"

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(lambda: asyncio.run(run(bind, "first", ThreadBridge())))
        assert await asyncio.to_thread(entered.wait, 3)
        try:
            with pytest.raises(WorkflowAdmissionError) as rejected:
                await run(bind, "first" if same_workflow else "second", Instant())
            assert rejected.value.code == ("WORKFLOW_ALREADY_RUNNING" if same_workflow
                                            else "WORKFLOW_CAPACITY_EXCEEDED")
            with Session(bind) as db:
                assert len(db.exec(select(WorkflowExecution)).all()) == 1
        finally:
            release.set()
        assert worker.result(timeout=3).status == "success"
    assert (await run(bind, "second", Instant())).status == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["commit", "refresh", "timer"])
async def test_review_initial_lifecycle_fault_releases_capacity_and_marks_history(review_db, monkeypatch, fault):
    bind, bridge = review_db, Instant()
    due = datetime(2026, 10, 20, tzinfo=UTC)
    with Session(bind) as db:
        job, owner = db.get(Workflow, "first"), db.get(User, "owner")
        original = getattr(db, fault) if fault != "timer" else None

        def fail(*args, **kwargs):
            raise RuntimeError(f"synthetic {fault}")

        with monkeypatch.context() as patch:
            if fault == "timer":
                patch.setattr(asyncio.get_running_loop(), "call_later", fail)
            else:
                patch.setattr(db, fault, fail)
            with pytest.raises(RuntimeError, match=f"synthetic {fault}"):
                await WorkflowEngine(bridge).execute(db, job, owner, triggered_by="cron", scheduled_due=due)
        if fault != "timer":
            assert getattr(db, fault) == original
    assert not has_active_workflow("first", "tenant", database_key(bind))
    assert bridge.calls == 0
    with Session(bind) as fresh:
        records = fresh.exec(select(WorkflowExecution)).all()
        if fault == "commit":
            assert not records
        else:
            assert len(records) == 1 and records[0].status == "failed"
    if fault == "commit":
        assert (await run(bind, "first", bridge, triggered_by="cron", scheduled_due=due)).status == "success"
    else:
        with pytest.raises(WorkflowAdmissionError, match="WORKFLOW_PERIOD_CONSUMED"):
            await run(bind, "first", bridge, triggered_by="cron", scheduled_due=due)
    assert (await run(bind, "second", Instant())).status == "success"


@pytest.mark.asyncio
async def test_review_delete_and_start_atomicity_across_threads(review_db, monkeypatch):
    bind = review_db
    entered, release = Event(), Event()

    class ThreadBridge:
        async def execute(self, session, owner, prompt):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            return "done"

    async def no_audit(*args, **kwargs):
        pass

    monkeypatch.setattr(routes, "audit_event", no_audit)
    with ThreadPoolExecutor(max_workers=1) as pool, Session(bind) as deleting:
        original = deleting.delete
        workers = []
        refused = []

        def start_between_check_and_delete(row):
            workers.append(pool.submit(lambda: asyncio.run(run(bind, "first", ThreadBridge()))))
            deadline = monotonic() + 3
            while not workers[-1].done() and not entered.is_set() and monotonic() < deadline:
                sleep(0.005)
            if workers[-1].done():
                try:
                    workers[-1].result()
                except WorkflowAdmissionError as exc:
                    refused.append(exc.code)
            else:
                assert entered.is_set(), "concurrent execution neither admitted nor refused"
            original(row)

        monkeypatch.setattr(deleting, "delete", start_between_check_and_delete)
        try:
            await routes.delete_workflow(Request({"type": "http"}), "first", deleting,
                                         deleting.get(User, "owner"))
        finally:
            release.set()
            for worker in workers:
                try:
                    worker.result(timeout=3)
                except RuntimeError:
                    pass
        with Session(bind) as fresh:
            if refused:
                assert fresh.get(Workflow, "first") is None
                assert fresh.exec(select(WorkflowExecution)).all() == []
                assert not entered.is_set()
            else:
                assert fresh.get(Workflow, "first") is not None, "delete removed an admitted running workflow"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure"])
async def test_review_terminal_due_not_replayed_and_next_due_allowed(review_db, outcome):
    from datetime import timedelta

    class Bridge(Instant):
        async def execute(self, session, owner, prompt):
            self.calls += 1
            if outcome == "failure":
                raise ValueError("synthetic failure")
            return "success"

    bridge = Bridge()
    due = datetime(2026, 10, 20, tzinfo=UTC)
    result = await run(review_db, "first", bridge, triggered_by="cron", scheduled_due=due)
    assert result.status == ("success" if outcome == "success" else "failed")
    before = bridge.calls
    for repeated in (due, due - timedelta(minutes=1)):
        with pytest.raises(WorkflowAdmissionError, match="WORKFLOW_PERIOD_CONSUMED"):
            await run(review_db, "first", bridge, triggered_by="cron", scheduled_due=repeated)
    assert bridge.calls == before
    await run(review_db, "first", bridge, triggered_by="cron", scheduled_due=due + timedelta(minutes=1))
    with Session(review_db) as fresh:
        assert len(fresh.exec(select(WorkflowExecution)).all()) == 2


@pytest.mark.asyncio
async def test_review_cancel_cleanup_holds_capacity_and_consumes_due(review_db):
    from app.workflow.control import cancel_execution

    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Bridge:
        async def execute(self, session, owner, prompt):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

    due = datetime(2026, 10, 20, tzinfo=UTC)
    running = asyncio.create_task(run(review_db, "first", Bridge(), triggered_by="cron", scheduled_due=due))
    await asyncio.wait_for(entered.wait(), 3)
    with Session(review_db) as fresh:
        eid = fresh.exec(select(WorkflowExecution)).one().id
    cancelling = asyncio.create_task(cancel_execution(eid, "first", "tenant", database_key(review_db)))
    await asyncio.wait_for(cleaning.wait(), 3)
    try:
        with pytest.raises(WorkflowAdmissionError, match="WORKFLOW_CAPACITY_EXCEEDED"):
            await run(review_db, "second", Instant())
        assert not cancelling.done() and not running.done()
    finally:
        release.set()
    assert await cancelling
    assert (await running).error == "WORKFLOW_CANCELLED"
    with pytest.raises(WorkflowAdmissionError, match="WORKFLOW_PERIOD_CONSUMED"):
        await run(review_db, "first", Instant(), triggered_by="cron", scheduled_due=due)
    assert (await run(review_db, "second", Instant())).status == "success"


@pytest.mark.asyncio
async def test_review_delete_commit_failure_releases_reservation_but_preserves_consumed_due(review_db, monkeypatch):
    due = datetime(2026, 10, 20, tzinfo=UTC)
    await run(review_db, "first", Instant(), triggered_by="cron", scheduled_due=due)
    with Session(review_db) as deleting:
        def fail():
            raise RuntimeError("synthetic delete commit")

        monkeypatch.setattr(deleting, "commit", fail)
        with pytest.raises(RuntimeError, match="synthetic delete commit"):
            await routes.delete_workflow(Request({"type": "http"}), "first", deleting,
                                         deleting.get(User, "owner"))
    with pytest.raises(WorkflowAdmissionError, match="WORKFLOW_PERIOD_CONSUMED"):
        await run(review_db, "first", Instant(), triggered_by="cron", scheduled_due=due)
    assert (await run(review_db, "first", Instant())).status == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["engine", "tenant"])
async def test_review_actual_engine_and_tenant_separate_period_identity(review_db, scope):
    due = datetime(2026, 10, 20, tzinfo=UTC)
    await run(review_db, "first", Instant(), triggered_by="cron", scheduled_due=due)
    second = None
    try:
        if scope == "engine":
            second = create_engine("sqlite://")
            SQLModel.metadata.create_all(second)
            with Session(second) as db:
                db.add(Tenant(id="tenant", name="tenant"))
                db.add(User(id="owner", tenant_id="tenant", username="owner", hashed_password=""))
                db.add(Workflow(id="first", tenant_id="tenant", owner_id="owner", name="first",
                                cron_expr="* * * * *", prompt_template="synthetic"))
                db.commit()
            assert (await run(second, "first", Instant(), triggered_by="cron", scheduled_due=due)).status == "success"
        else:
            with Session(review_db) as db:
                db.add(Tenant(id="other", name="other"))
                owner = db.get(User, "owner")
                owner.tenant_id = "other"
                job = db.get(Workflow, "first")
                job.tenant_id = "other"
                db.add(owner)
                db.add(job)
                db.commit()
            result = await run(review_db, "first", Instant(), triggered_by="cron", scheduled_due=due)
            assert result.status == "success"
    finally:
        if second is not None:
            second.dispose()
