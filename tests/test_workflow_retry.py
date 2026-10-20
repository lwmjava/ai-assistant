"""工作流失败重试：同一条执行记录内的指数退避与上限。"""

import math

import pytest
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution
from app.workflow.engine import WorkflowEngine, workflow_retry_delay, workflow_retry_limit


class CountingBridge:
    def __init__(self, failures: int, fail: Exception | None = None) -> None:
        self.failures = failures
        self.fail = fail or RuntimeError("temporary")
        self.calls = 0

    async def execute(self, session: Session, owner: User, prompt: str) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.fail
        return "recovered"


def _job(persist_identity, *, active: bool = True) -> tuple[Workflow, User]:
    owner = User(
        id="retry-owner",
        tenant_id="retry-tenant",
        username="retry-owner",
        hashed_password="",
        role=Role.MEMBER.value,
        is_active=active,
    )
    persist_identity(owner)
    workflow = Workflow(
        id="retry-job",
        tenant_id=owner.tenant_id,
        owner_id=owner.id,
        name="retry",
        cron_expr="0 0 1 1 *",
        prompt_template="hello",
    )
    with Session(engine) as session:
        session.add(workflow)
        session.commit()
        session.refresh(workflow)
        session.expunge(workflow)
    return workflow, owner


@pytest.fixture(autouse=True)
def _restore_retry(monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 0)
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_BASE_SECONDS", 1.0)
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX_DELAY_SECONDS", 8.0)
    yield
    with Session(engine) as session:
        for row in session.exec(select(WorkflowExecution)).all():
            if row.workflow_id == "retry-job":
                session.delete(row)
        job = session.get(Workflow, "retry-job")
        if job is not None:
            session.delete(job)
        session.commit()


def test_retry_bounds_reject_extremes(monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", -1)
    assert workflow_retry_limit() == 0
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 99)
    assert workflow_retry_limit() == 3
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_BASE_SECONDS", math.nan)
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX_DELAY_SECONDS", math.inf)
    assert workflow_retry_delay(3) == 0.0
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_BASE_SECONDS", 30)
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX_DELAY_SECONDS", 100)
    assert workflow_retry_delay(1) == 30.0
    assert workflow_retry_delay(3) == 30.0


@pytest.mark.asyncio
async def test_transient_failure_retries_and_records_backoff(persist_identity, monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 1)
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_BASE_SECONDS", 2)
    workflow, owner = _job(persist_identity)
    bridge = CountingBridge(failures=1)
    waits: list[float] = []

    async def sleeper(delay: float) -> None:
        waits.append(delay)

    with Session(engine) as session:
        result = await WorkflowEngine(bridge=bridge).execute(session, workflow, owner, sleeper=sleeper)
        rows = session.exec(select(WorkflowExecution).where(col(WorkflowExecution.workflow_id) == workflow.id)).all()
    assert bridge.calls == 2
    assert waits == [2.0]
    assert len(rows) == 1
    assert result.status == "success"
    assert result.error is None
    assert result.output == "recovered"
    assert result.tenant_id == owner.tenant_id
    assert result.triggered_by == "manual"


@pytest.mark.asyncio
async def test_persistent_failure_stops_at_limit(persist_identity, monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 2)
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_BASE_SECONDS", 1)
    workflow, owner = _job(persist_identity)
    bridge = CountingBridge(failures=9, fail=RuntimeError("still down"))
    waits: list[float] = []

    async def sleeper(delay: float) -> None:
        waits.append(delay)

    with Session(engine) as session:
        result = await WorkflowEngine(bridge=bridge).execute(session, workflow, owner, sleeper=sleeper)
        rows = session.exec(select(WorkflowExecution).where(col(WorkflowExecution.workflow_id) == workflow.id)).all()
    assert bridge.calls == 3
    assert waits == [1.0, 2.0]
    assert len(rows) == 1
    assert result.status == "failed"
    assert result.error == "已达到重试上限 2：still down"


@pytest.mark.asyncio
async def test_zero_retry_calls_bridge_once(persist_identity):
    workflow, owner = _job(persist_identity)
    bridge = CountingBridge(failures=1)
    waits: list[float] = []

    async def sleeper(delay: float) -> None:
        waits.append(delay)

    with Session(engine) as session:
        result = await WorkflowEngine(bridge=bridge).execute(session, workflow, owner, sleeper=sleeper)
    assert bridge.calls == 1
    assert waits == []
    assert result.status == "failed"
    assert "已达到重试上限 0" in (result.error or "")


@pytest.mark.asyncio
async def test_inactive_owner_does_not_call_bridge(persist_identity):
    workflow, owner = _job(persist_identity, active=False)
    bridge = CountingBridge(failures=0)

    with Session(engine) as session:
        result = await WorkflowEngine(bridge=bridge).execute(session, workflow, owner)
        current = session.get(Workflow, workflow.id)
    assert bridge.calls == 0
    assert result.status == "failed"
    assert "创建者已失效" in (result.error or "")
    assert current is not None and current.enabled is False and current.suspended_owner is True
