"""Independent entry-level adversarial review, workflow-timeout-cancel-v1 (E2)."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.api.deps import get_current_user
from app.api.routes import workflow as routes
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.main import app
from app.models.user import Tenant, User
from app.models.workflow import Workflow, WorkflowExecution
from app.workflow.control import cancel_execution, database_key, has_active_workflow
from app.workflow.engine import WorkflowEngine


@pytest.fixture
def reviewed_job(persist_identity, monkeypatch):
    suffix = uuid4().hex
    actor = User(id=suffix, tenant_id=suffix, username=suffix,
                 hashed_password="", role=Role.TENANT_ADMIN.value, is_active=True)
    persist_identity(actor)
    job = Workflow(id=suffix, owner_id=actor.id, tenant_id=actor.tenant_id,
                   name="review", cron_expr="* * * * *", prompt_template="synthetic",
                   timeout_seconds=1)
    with Session(engine) as db:
        db.add(job)
        db.commit()
        db.refresh(job)
        db.expunge(job)
    monkeypatch.setattr(settings, "WORKFLOW_ENABLED", True)
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_user] = lambda: actor
    yield job, actor
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    with Session(engine) as db:
        for row in db.exec(select(WorkflowExecution).where(
                col(WorkflowExecution.workflow_id) == job.id)).all():
            db.delete(row)
        row = db.get(Workflow, job.id)
        if row is not None:
            db.delete(row)
        db.commit()


class GateBridge:
    def __init__(self):
        self.entered = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def execute(self, session, owner, prompt):
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleaning.set()
            await self.release.wait()
            self.closed = True


def execution_id(job):
    with Session(engine) as db:
        return db.exec(select(WorkflowExecution).where(
            col(WorkflowExecution.workflow_id) == job.id)).one().id


@pytest.mark.asyncio
async def test_review_http_duplicate_cancel_and_delete_wait_for_actual_cleanup(reviewed_job, monkeypatch):
    job, _actor = reviewed_job
    bridge = GateBridge()
    monkeypatch.setattr(routes, "WorkflowEngine", lambda: WorkflowEngine(bridge))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        running = asyncio.create_task(client.post(f"/api/v1/workflows/{job.id}/run"))
        await asyncio.wait_for(bridge.entered.wait(), 3)
        eid = execution_id(job)
        endpoint = f"/api/v1/workflows/{job.id}/executions/{eid}/cancel"
        cancellations = [asyncio.create_task(client.post(endpoint)) for _ in range(2)]
        await asyncio.wait_for(bridge.cleaning.wait(), 3)
        await asyncio.sleep(0.03)
        assert all(not task.done() for task in cancellations)
        assert (await client.delete(f"/api/v1/workflows/{job.id}")).status_code == 409
        assert has_active_workflow(job.id, job.tenant_id, database_key(engine))
        bridge.release.set()
        replies = await asyncio.gather(*cancellations)
        assert all(reply.status_code == 200 for reply in replies)
        assert all(reply.json()["error"] == "WORKFLOW_CANCELLED" for reply in replies)
        assert (await running).json()["error"] == "WORKFLOW_CANCELLED"
        assert bridge.closed
        assert not has_active_workflow(job.id, job.tenant_id, database_key(engine))
        assert (await client.delete(f"/api/v1/workflows/{job.id}")).status_code == 200


@pytest.mark.asyncio
async def test_review_timeout_cannot_finish_before_cleanup_and_parent_repeat_cancel(reviewed_job):
    job, actor = reviewed_job
    bridge = GateBridge()
    with Session(engine) as db:
        running = asyncio.create_task(WorkflowEngine(bridge).execute(db, job, actor))
        await asyncio.wait_for(bridge.entered.wait(), 3)
        eid = execution_id(job)
        await asyncio.wait_for(bridge.cleaning.wait(), 3)
        for _ in range(3):
            running.cancel()
            await asyncio.sleep(0)
        assert not running.done()
        assert has_active_workflow(job.id, job.tenant_id, database_key(engine))
        bridge.release.set()
        with pytest.raises(asyncio.CancelledError):
            await running
    with Session(engine) as fresh:
        row = fresh.get(WorkflowExecution, eid)
        assert row.error == "WORKFLOW_TIMEOUT" and row.status == "failed"
        assert row.finished_at and row.duration_ms >= 900
    assert bridge.closed
    assert not has_active_workflow(job.id, job.tenant_id, database_key(engine))


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["owner_disabled", "owner_reassigned", "tenant_disabled"])
async def test_review_real_engine_rechecks_identity_after_backoff(reviewed_job, monkeypatch, revocation):
    job, actor = reviewed_job
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 1)

    class RejectBridge:
        calls = 0

        async def execute(self, session, owner, prompt):
            self.calls += 1
            raise ValueError("synthetic retry")

    async def revoke(_delay):
        with Session(engine) as fresh:
            if revocation == "tenant_disabled":
                row = fresh.get(Tenant, actor.tenant_id)
                row.is_active = False
            else:
                row = fresh.get(User, actor.id)
                if revocation == "owner_disabled":
                    row.is_active = False
                else:
                    row.tenant_id = "different-tenant"
            fresh.add(row)
            fresh.commit()

    bridge = RejectBridge()
    with Session(engine) as db:
        result = await WorkflowEngine(bridge).execute(db, job, actor, sleeper=revoke)
        eid = result.id
    with Session(engine) as fresh:
        row = fresh.get(WorkflowExecution, eid)
        assert row.status == "failed" and row.finished_at
        if revocation == "tenant_disabled":
            assert row.error == "WORKFLOW_TENANT_UNAVAILABLE"
    assert bridge.calls == 1
    assert not has_active_workflow(job.id, job.tenant_id, database_key(engine))


@pytest.mark.asyncio
async def test_review_real_terminal_commit_failure_returns_503_without_fake_success(reviewed_job, monkeypatch):
    job, actor = reviewed_job
    bridge = GateBridge()
    with Session(engine) as owner_session:
        running = asyncio.create_task(WorkflowEngine(bridge).execute(owner_session, job, actor))
        await asyncio.wait_for(bridge.entered.wait(), 3)
        eid = execution_id(job)

        def unavailable():
            raise OperationalError("synthetic unavailable", {}, RuntimeError("private failure"))

        monkeypatch.setattr(owner_session, "commit", unavailable)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            cancelling = asyncio.create_task(client.post(
                f"/api/v1/workflows/{job.id}/executions/{eid}/cancel"))
            await asyncio.wait_for(bridge.cleaning.wait(), 3)
            bridge.release.set()
            reply = await cancelling
            assert reply.status_code == 503
            assert "private failure" not in reply.text
        with pytest.raises(OperationalError):
            await running
    assert bridge.closed
    assert not has_active_workflow(job.id, job.tenant_id, database_key(engine))
    with Session(engine) as fresh:
        row = fresh.get(WorkflowExecution, eid)
        assert row.status == "running" and row.finished_at is None


@pytest.mark.asyncio
async def test_review_identical_urls_and_ids_cannot_cancel_other_memory_database():
    first, second = create_engine("sqlite://"), create_engine("sqlite://")
    bridge = GateBridge()
    bridge.release.set()
    try:
        for bind in (first, second):
            SQLModel.metadata.create_all(bind)
            with Session(bind) as db:
                db.add(Tenant(id="same", name="same"))
                db.add(User(id="same", tenant_id="same", username="same", hashed_password=""))
                db.add(Workflow(id="same", tenant_id="same", owner_id="same", name="same",
                                cron_expr="* * * * *", prompt_template="same"))
                db.commit()
        with Session(first) as owner_db:
            running = asyncio.create_task(WorkflowEngine(bridge).execute(
                owner_db, owner_db.get(Workflow, "same"), owner_db.get(User, "same")))
            await asyncio.wait_for(bridge.entered.wait(), 3)
            eid = owner_db.exec(select(WorkflowExecution)).one().id
            with Session(second) as other_db:
                other_db.add(WorkflowExecution(id=eid, workflow_id="same", tenant_id="same",
                                               status="running"))
                other_db.commit()
            assert not await cancel_execution(eid, "same", "same", database_key(second))
            assert not running.done()
            assert await cancel_execution(eid, "same", "same", database_key(first))
            assert (await running).error == "WORKFLOW_CANCELLED"
            with Session(second) as other_db:
                assert other_db.get(WorkflowExecution, eid).status == "running"
        assert not has_active_workflow("same", "same", database_key(first))
        assert bridge.closed
    finally:
        first.dispose()
        second.dispose()
