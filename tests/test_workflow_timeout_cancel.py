"""工作流总时限、协作取消与资源释放场景，合成 Smoke/Adversarial v1。"""

import asyncio

import pytest
from sqlmodel import Session, col, select

from app.core.database import engine
from app.core.security import Role
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution
from app.workflow.control import database_key
from app.workflow.engine import WorkflowEngine


class LongBridge:
    def __init__(self):
        self.entered = asyncio.Event()
        self.released = asyncio.Event()
        self.calls = 0

    async def execute(self, session, owner, prompt):
        self.calls += 1
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.released.set()


@pytest.fixture
def job(persist_identity):
    owner = User(id="timeout-owner", tenant_id="timeout-tenant", username="timeout-owner",
                 hashed_password="", role=Role.MEMBER.value, is_active=True)
    persist_identity(owner)
    wf = Workflow(id="timeout-job", owner_id=owner.id, tenant_id=owner.tenant_id,
                  name="long", cron_expr="* * * * *", prompt_template="hello", timeout_seconds=1)
    with Session(engine) as session:
        session.add(wf)
        session.commit()
        session.refresh(wf)
        session.expunge(wf)
    yield wf, owner
    with Session(engine) as session:
        for row in session.exec(select(WorkflowExecution).where(col(WorkflowExecution.workflow_id) == wf.id)).all():
            session.delete(row)
        row = session.get(Workflow, wf.id)
        if row:
            session.delete(row)
        session.commit()


@pytest.mark.asyncio
async def test_total_timeout_releases_and_persists(job):
    wf, owner = job
    bridge = LongBridge()
    with Session(engine) as session:
        task = asyncio.create_task(WorkflowEngine(bridge=bridge).execute(session, wf, owner))
        await asyncio.wait_for(bridge.entered.wait(), 2)
        done, _ = await asyncio.wait({task}, timeout=1.3)
        completed_on_deadline = bool(done)
        if not task.done():
            task.cancel()
        try:
            result = await task
        except asyncio.CancelledError:
            result = None
    assert completed_on_deadline, "执行超过配置总时限仍未终止"
    assert result is not None and result.status == "failed"
    assert result.error == "WORKFLOW_TIMEOUT"
    assert bridge.released.is_set()
    with Session(engine) as fresh:
        row = fresh.get(WorkflowExecution, result.id)
        assert row.status == "failed" and row.error == "WORKFLOW_TIMEOUT"
        assert row.finished_at is not None and row.duration_ms >= 900


def active_id(wf):
    with Session(engine) as session:
        row = session.exec(select(WorkflowExecution).where(col(WorkflowExecution.workflow_id) == wf.id)).one()
        return row.id


async def cancel(wf, execution_id):
    from app.workflow.control import cancel_execution
    return await cancel_execution(execution_id, wf.id, wf.tenant_id, database_key(engine))


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["manual", "cron"])
async def test_cancel_across_instances_and_repeat_is_idempotent(job, source):
    from app.workflow.control import has_active_workflow
    wf, owner = job
    bridge = LongBridge()
    with Session(engine) as session:
        task = asyncio.create_task(WorkflowEngine(bridge=bridge).execute(session, wf, owner, triggered_by=source))
        await asyncio.wait_for(bridge.entered.wait(), 2)
        execution_id = active_id(wf)
        results = await asyncio.gather(cancel(wf, execution_id), cancel(wf, execution_id))
        result = await task
    assert any(results)
    assert result.status == "failed" and result.error == "WORKFLOW_CANCELLED"
    assert result.triggered_by == source
    assert bridge.released.is_set() and bridge.calls == 1
    assert not has_active_workflow(wf.id, wf.tenant_id, database_key(engine))
    assert not await cancel(wf, execution_id)
    with Session(engine) as fresh:
        row = fresh.get(WorkflowExecution, execution_id)
        assert row.error == "WORKFLOW_CANCELLED" and row.finished_at and row.duration_ms is not None


@pytest.mark.asyncio
async def test_wrong_binding_cannot_cancel(job):
    from app.workflow.control import cancel_execution
    wf, owner = job
    bridge = LongBridge()
    with Session(engine) as session:
        task = asyncio.create_task(WorkflowEngine(bridge=bridge).execute(session, wf, owner))
        await asyncio.wait_for(bridge.entered.wait(), 2)
        eid = active_id(wf)
        for wid, tenant, db in [("wrong", wf.tenant_id, database_key(engine)),
                                (wf.id, "wrong", database_key(engine)), (wf.id, wf.tenant_id, "wrong")]:
            assert not await cancel_execution(eid, wid, tenant, db)
        assert not task.done()
        await cancel(wf, eid)
        await task


@pytest.mark.asyncio
async def test_parent_repeat_cancel_does_not_interrupt_child_cleanup(job):
    from app.workflow.control import has_active_workflow
    wf, owner = job
    cleaning, allow_cleanup = asyncio.Event(), asyncio.Event()

    class CleanupBridge(LongBridge):
        async def execute(self, session, owner, prompt):
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await allow_cleanup.wait()
                self.released.set()

    bridge = CleanupBridge()
    with Session(engine) as session:
        task = asyncio.create_task(WorkflowEngine(bridge=bridge).execute(session, wf, owner))
        await asyncio.wait_for(bridge.entered.wait(), 2)
        eid = active_id(wf)
        task.cancel()
        await asyncio.wait_for(cleaning.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and has_active_workflow(wf.id, wf.tenant_id, database_key(engine))
        allow_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert bridge.released.is_set()
    with Session(engine) as fresh:
        assert fresh.get(WorkflowExecution, eid).error == "WORKFLOW_CANCELLED"
    assert not has_active_workflow(wf.id, wf.tenant_id, database_key(engine))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "timeout"])
async def test_backoff_is_part_of_total_execution(job, monkeypatch, mode):
    from app.core.config import settings
    wf, owner = job
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 3)
    waiting, released = asyncio.Event(), asyncio.Event()

    class FailingBridge:
        calls = 0
        async def execute(self, session, owner, prompt):
            self.calls += 1
            raise RuntimeError("temporary")

    async def pause(delay):
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    bridge = FailingBridge()
    with Session(engine) as session:
        task = asyncio.create_task(WorkflowEngine(bridge=bridge).execute(session, wf, owner, sleeper=pause))
        await asyncio.wait_for(waiting.wait(), 2)
        if mode == "cancel":
            await cancel(wf, active_id(wf))
        result = await asyncio.wait_for(task, 3)
    assert result.error == ("WORKFLOW_CANCELLED" if mode == "cancel" else "WORKFLOW_TIMEOUT")
    assert bridge.calls == 1 and released.is_set()


@pytest.mark.asyncio
async def test_cancellation_rolls_back_failed_transaction(job):
    from sqlalchemy.exc import IntegrityError
    wf, owner = job

    class BrokenTransaction(LongBridge):
        async def execute(self, session, actor, prompt):
            duplicate = Workflow(id=wf.id, owner_id=actor.id, tenant_id=actor.tenant_id,
                                 name="duplicate", cron_expr="* * * * *", prompt_template="x")
            session.add(duplicate)
            try:
                session.flush()
            except IntegrityError:
                pass
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.released.set()

    bridge = BrokenTransaction()
    with Session(engine) as session:
        task = asyncio.create_task(WorkflowEngine(bridge=bridge).execute(session, wf, owner))
        await asyncio.wait_for(bridge.entered.wait(), 2)
        eid = active_id(wf)
        await cancel(wf, eid)
        result = await task
    assert result.error == "WORKFLOW_CANCELLED" and bridge.released.is_set()
    with Session(engine) as fresh:
        assert fresh.get(WorkflowExecution, eid).status == "failed"
        assert fresh.get(Workflow, wf.id).name == "long"


@pytest.mark.asyncio
async def test_owner_deleted_does_not_impersonate_triggerer(job):
    wf, owner = job
    with Session(engine) as session:
        session.delete(session.get(User, owner.id))
        session.commit()
    bridge = LongBridge()
    with Session(engine) as session:
        result = await WorkflowEngine(bridge=bridge).execute(session, wf, owner)
    assert bridge.calls == 0 and result.status == "failed"


@pytest.mark.asyncio
async def test_bridge_and_real_chatservice_propagate_cancellation(job, monkeypatch):
    from app.agents.pipeline import AgentPipeline
    from app.llm.factory import set_llm_provider_override
    from app.llm.mock import MockLLMProvider
    from app.models.conversation import Conversation, Message
    from app.workflow.bridge import WorkflowBridge
    wf, owner = job
    entered, released = asyncio.Event(), asyncio.Event()

    async def long_run(self, state):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    monkeypatch.setattr(AgentPipeline, "run", long_run)
    set_llm_provider_override(MockLLMProvider())
    try:
        with Session(engine) as session:
            task = asyncio.create_task(WorkflowEngine(bridge=WorkflowBridge()).execute(session, wf, owner))
            await asyncio.wait_for(entered.wait(), 4)
            await cancel(wf, active_id(wf))
            result = await task
        assert result.error == "WORKFLOW_CANCELLED" and released.is_set()
        with Session(engine) as fresh:
            conversations = fresh.exec(select(Conversation).where(col(Conversation.user_id) == owner.id)).all()
            assert conversations
            for conv in conversations:
                messages = fresh.exec(select(Message).where(col(Message.conversation_id) == conv.id)).all()
                assert not any(m.role == "assistant" for m in messages)
                for message in messages:
                    fresh.delete(message)
                fresh.delete(conv)
            fresh.commit()
    finally:
        set_llm_provider_override(None)


@pytest.mark.asyncio
async def test_cron_trigger_cancel_does_not_cancel_caller(job, monkeypatch):
    from app.workflow import scheduler
    wf, _owner = job
    bridge = LongBridge()
    monkeypatch.setattr(scheduler, "WorkflowEngine", lambda: WorkflowEngine(bridge=bridge))
    task = asyncio.create_task(scheduler._trigger(wf))
    await asyncio.wait_for(bridge.entered.wait(), 2)
    eid = active_id(wf)
    await cancel(wf, eid)
    await task
    assert not task.cancelled() and bridge.released.is_set()
    with Session(engine) as session:
        row = session.get(WorkflowExecution, eid)
        assert row.triggered_by == "cron" and row.error == "WORKFLOW_CANCELLED"


@pytest.mark.asyncio
async def test_swallowed_cancel_cannot_be_marked_success(job):
    wf, owner = job

    class SwallowBridge(LongBridge):
        async def execute(self, session, owner, prompt):
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "should not succeed"
            finally:
                self.released.set()

    bridge = SwallowBridge()
    with Session(engine) as session:
        task = asyncio.create_task(WorkflowEngine(bridge=bridge).execute(session, wf, owner))
        await asyncio.wait_for(bridge.entered.wait(), 2)
        await cancel(wf, active_id(wf))
        row = await task
    assert row.status == "failed" and row.error == "WORKFLOW_CANCELLED" and row.output is None
    assert bridge.released.is_set()


@pytest.mark.asyncio
async def test_expired_owner_during_backoff_is_rechecked_before_bridge(job, monkeypatch):
    from app.core.config import settings
    wf, owner = job
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 1)

    class FailingBridge:
        calls = 0
        async def execute(self, session, actor, prompt):
            self.calls += 1
            raise RuntimeError("temporary")

    async def revoke(delay):
        with Session(engine) as fresh:
            actor = fresh.get(User, owner.id)
            actor.is_active = False
            fresh.add(actor)
            fresh.commit()

    bridge = FailingBridge()
    with Session(engine) as session:
        row = await WorkflowEngine(bridge=bridge).execute(session, wf, owner, sleeper=revoke)
    assert bridge.calls == 1 and row.status == "failed"
    with Session(engine) as fresh:
        assert fresh.get(Workflow, wf.id).suspended_owner
