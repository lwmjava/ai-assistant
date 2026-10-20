"""workflow-timeout-cancel-v1：隔离数据库中的 HTTP 契约与租户拒绝。"""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.api.routes import workflow as routes
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.main import app
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution
from app.workflow.engine import WorkflowEngine


@pytest.fixture
def principal(persist_identity, monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_ENABLED", True)
    owner = User(id="cancel-owner", tenant_id="cancel-tenant", username="cancel-owner",
                 hashed_password="", role=Role.TENANT_ADMIN.value, is_active=True)
    persist_identity(owner)
    app.dependency_overrides[get_current_user] = lambda: owner
    yield owner
    with Session(engine) as session:
        for record in session.exec(select(WorkflowExecution)).all():
            session.delete(record)
        for job in session.exec(select(Workflow)).all():
            session.delete(job)
        session.commit()


@pytest.fixture
async def client(principal):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        yield client


async def create(client, **kwargs):
    return await client.post("/api/v1/workflows", json={
        "name": "cancel", "cron_expr": "0 0 1 1 *", "prompt_template": "private prompt", **kwargs})


@pytest.mark.asyncio
async def test_timeout_defaults_boundaries_and_persistence(client):
    created = await create(client)
    assert created.status_code == 201
    workflow_id = created.json()["id"]
    assert created.json()["timeout_seconds"] == 300
    for value in (1, 3600):
        updated = await client.put(f"/api/v1/workflows/{workflow_id}",
                                   json={"timeout_seconds": value})
        assert updated.status_code == 200
        with Session(engine) as session:
            assert session.get(Workflow, workflow_id).timeout_seconds == value
    updated = await client.put(f"/api/v1/workflows/{workflow_id}", json={"name": "renamed"})
    assert updated.status_code == 200
    assert updated.json()["timeout_seconds"] == 3600
    schema = app.openapi()["components"]["schemas"]["WorkflowUpdate"]
    assert schema["properties"]["timeout_seconds"]["type"] == "integer"
    assert "anyOf" not in schema["properties"]["timeout_seconds"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, -1, 3601, True, False, 1.5, "20", None])
async def test_timeout_rejects_invalid_create_and_update(client, value):
    assert (await create(client, timeout_seconds=value)).status_code == 422
    workflow_id = (await create(client)).json()["id"]
    updated = await client.put(f"/api/v1/workflows/{workflow_id}",
                               json={"timeout_seconds": value})
    assert updated.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_timeout_rejects_nonfinite_raw_json_without_writes(client, value, operation):
    workflow_id = (await create(client)).json()["id"]
    with Session(engine) as session:
        before = len(session.exec(select(Workflow)).all())
    raw_create = ('{"name":"nonfinite","cron_expr":"0 0 1 1 *",'
                  '"prompt_template":"private prompt","timeout_seconds":' + value + '}')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://test") as raw_client:
        if operation == "create":
            response = await raw_client.post("/api/v1/workflows", content=raw_create,
                                             headers={"Content-Type": "application/json"})
        else:
            response = await raw_client.put(f"/api/v1/workflows/{workflow_id}",
                                            content='{"timeout_seconds":' + value + '}',
                                            headers={"Content-Type": "application/json"})
    with Session(engine) as session:
        assert len(session.exec(select(Workflow)).all()) == before
        assert session.get(Workflow, workflow_id).timeout_seconds == 300
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cancel_waits_for_cleanup_returns_fresh_terminal_and_audits(client, principal, monkeypatch):
    started, released = asyncio.Event(), asyncio.Event()

    class Bridge:
        async def execute(self, session, owner, prompt):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                released.set()

    workflow_id = (await create(client)).json()["id"]
    monkeypatch.setattr(routes, "WorkflowEngine", lambda: WorkflowEngine(bridge=Bridge()))
    audit = AsyncMock()
    monkeypatch.setattr(routes, "audit_event", audit)
    running = asyncio.create_task(client.post(f"/api/v1/workflows/{workflow_id}/run"))
    await asyncio.wait_for(started.wait(), 3)
    history = (await client.get(f"/api/v1/workflows/{workflow_id}/executions")).json()
    execution_id = history[0]["id"]
    assert (await client.delete(f"/api/v1/workflows/{workflow_id}")).status_code == 409
    cancelled = await client.post(f"/api/v1/workflows/{workflow_id}/executions/{execution_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "failed"
    assert cancelled.json()["error"] == "WORKFLOW_CANCELLED"
    assert released.is_set()
    assert (await running).json()["status"] == "failed"
    repeated = await client.post(f"/api/v1/workflows/{workflow_id}/executions/{execution_id}/cancel")
    assert repeated.json() == cancelled.json()
    cancel_calls = [c for c in audit.call_args_list if c.kwargs.get("details", {}).get("operation") == "cancel"]
    assert cancel_calls
    assert "private prompt" not in str(cancel_calls)
    with Session(engine) as session:
        record = session.get(WorkflowExecution, execution_id)
        assert record.status == "failed" and record.finished_at is not None
    assert (await client.delete(f"/api/v1/workflows/{workflow_id}")).status_code == 200


@pytest.mark.asyncio
async def test_cancel_wrong_tenant_binding_missing_runtime_and_terminal(client, principal, monkeypatch):
    workflow_id = (await create(client)).json()["id"]
    other_id = (await create(client)).json()["id"]
    with Session(engine) as session:
        session.add(WorkflowExecution(id="orphan", workflow_id=workflow_id,
                                     tenant_id=principal.tenant_id, status="running"))
        session.add(WorkflowExecution(id="wrong-tenant", workflow_id=workflow_id,
                                     tenant_id="other", status="success"))
        session.add(WorkflowExecution(id="terminal", workflow_id=workflow_id,
                                     tenant_id=principal.tenant_id, status="success", output="completed"))
        session.commit()
    base = f"/api/v1/workflows/{workflow_id}/executions"
    assert (await client.post(f"{base}/orphan/cancel")).status_code == 409
    assert (await client.post(f"{base}/wrong-tenant/cancel")).status_code == 404
    assert (await client.post(f"{base}/missing/cancel")).status_code == 404
    assert (await client.post(
        f"/api/v1/workflows/{other_id}/executions/terminal/cancel")).status_code == 404
    assert (await client.post(f"{base}/terminal/cancel")).json()["output"] == "completed"
    principal.tenant_id = "another-tenant"
    assert (await client.post(f"{base}/terminal/cancel")).status_code == 404
    principal.role = Role.SYSTEM_ADMIN.value
    assert (await client.post(f"{base}/terminal/cancel")).status_code == 200
    principal.tenant_id = "cancel-tenant"
    principal.role = Role.SYSTEM_VIEWER.value
    assert (await client.post(f"{base}/terminal/cancel")).status_code == 403
    app.dependency_overrides.pop(get_current_user)
    assert (await client.post(f"{base}/terminal/cancel")).status_code == 401


@pytest.mark.asyncio
async def test_missing_owner_never_falls_back_to_triggerer(client, principal):
    workflow_id = (await create(client)).json()["id"]
    with Session(engine) as session:
        workflow = session.get(Workflow, workflow_id)
        workflow.owner_id = "deleted-owner"
        session.add(workflow)
        session.commit()
    assert (await client.post(f"/api/v1/workflows/{workflow_id}/run")).status_code == 409


@pytest.mark.asyncio
async def test_cancel_terminal_persistence_failure_returns_safe_503(client, principal, monkeypatch):
    workflow_id = (await create(client)).json()["id"]
    with Session(engine) as session:
        session.add(WorkflowExecution(id="persistence-error", workflow_id=workflow_id,
                                     tenant_id=principal.tenant_id, status="running"))
        session.commit()
    monkeypatch.setattr(routes, "cancel_execution", AsyncMock(side_effect=RuntimeError("secret")), raising=False)
    response = await client.post(f"/api/v1/workflows/{workflow_id}/executions/persistence-error/cancel")
    assert response.status_code == 503
    assert "secret" not in response.text
