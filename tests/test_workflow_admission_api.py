"""workflow-concurrency-idempotency-v1：真实HTTP准入与配置失败边界。"""

import asyncio

import httpx
import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.api.routes import workflow as routes
from app.core.config import Settings, settings
from app.core.database import engine
from app.core.security import Role
from app.main import app
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution
from app.workflow.engine import WorkflowEngine


@pytest.mark.parametrize("value", [True, False, 1.0, 1.5, float("nan"), float("inf"),
                                  float("-inf"), 0, -1, 1001, "1.0", "NaN"])
def test_workflow_concurrency_config_rejects_invalid(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, WORKFLOW_MAX_CONCURRENCY=value)


def test_workflow_concurrency_default_and_env_integer(monkeypatch):
    monkeypatch.delenv("WORKFLOW_MAX_CONCURRENCY", raising=False)
    assert Settings(_env_file=None).WORKFLOW_MAX_CONCURRENCY == 4
    for value in ("1", "1000"):
        monkeypatch.setenv("WORKFLOW_MAX_CONCURRENCY", value)
        assert Settings(_env_file=None).WORKFLOW_MAX_CONCURRENCY == int(value)


@pytest.fixture
def principal(persist_identity, monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_ENABLED", True)
    monkeypatch.setitem(settings.__dict__, "WORKFLOW_MAX_CONCURRENCY", 1)
    owner = User(id="admission-api-owner", tenant_id="admission-api-tenant",
                 username="admission-api-owner", hashed_password="",
                 role=Role.TENANT_ADMIN.value, is_active=True)
    persist_identity(owner)
    app.dependency_overrides[get_current_user] = lambda: owner
    yield owner
    with Session(engine) as session:
        for record in session.exec(select(WorkflowExecution)).all():
            session.delete(record)
        for workflow in session.exec(select(Workflow)).all():
            session.delete(workflow)
        session.commit()


@pytest.mark.asyncio
async def test_http_reentry_and_capacity_refuse_without_new_history(principal, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    class Bridge:
        calls = 0

        async def execute(self, session, owner, prompt):
            self.calls += 1
            if self.calls == 1:
                entered.set()
                await release.wait()
            return "done"

    bridge = Bridge()
    monkeypatch.setattr(routes, "WorkflowEngine", lambda: WorkflowEngine(bridge=bridge))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        jobs = []
        for name in ("first", "second"):
            created = await client.post("/api/v1/workflows", json={
                "name": name, "cron_expr": "0 0 1 1 *", "prompt_template": name})
            assert created.status_code == 201
            jobs.append(created.json()["id"])
        running = asyncio.create_task(client.post(f"/api/v1/workflows/{jobs[0]}/run"))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            duplicate = await client.post(f"/api/v1/workflows/{jobs[0]}/run")
            assert duplicate.status_code == 409
            capacity = await client.post(f"/api/v1/workflows/{jobs[1]}/run")
            assert capacity.status_code == 409
            assert bridge.calls == 1
            with Session(engine) as session:
                assert len(session.exec(select(WorkflowExecution)).all()) == 1
        finally:
            release.set()
            await running
        assert (await client.post(f"/api/v1/workflows/{jobs[1]}/run")).status_code == 201
        assert bridge.calls == 2
