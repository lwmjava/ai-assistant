"""工作流告警安全与文档契约回归，合成Adversarial/Smoke v1。"""

import json
import uuid
from pathlib import Path

import pytest
from sqlmodel import Session, col, select

from app.audit.models import WorkflowExecutionAlert, WorkflowExecutionLog
from app.core.config import settings
from app.core.database import engine
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution
from app.workflow.engine import WorkflowEngine


@pytest.fixture
def privacy_job(persist_identity):
    key = uuid.uuid4().hex
    owner = User(id=f"privacy-{key}", tenant_id=f"privacy-{key}", username=f"privacy-{key}",
                 hashed_password="", is_active=True)
    persist_identity(owner)
    with Session(engine) as session:
        wf = Workflow(owner_id=owner.id, tenant_id=owner.tenant_id, name="synthetic privacy job",
                      cron_expr="* * * * *", prompt_template="safe prompt")
        session.add(wf)
        session.commit()
        session.refresh(wf)
        session.expunge(wf)
    yield wf, owner
    with Session(engine) as session:
        for model in (WorkflowExecutionAlert, WorkflowExecutionLog, WorkflowExecution):
            for row in session.exec(select(model).where(col(model.workflow_id) == wf.id)).all():
                session.delete(row)
        persisted = session.get(Workflow, wf.id)
        if persisted:
            session.delete(persisted)
        session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    "Authorization: Bearer SYNTHETIC_BEARER_7e9a",
    "api_key=SYNTHETIC_API_KEY_7e9a",
    "https://synthetic-user:SYNTHETIC_PASSWORD_7e9a@example.invalid/?token=SYNTHETIC_TOKEN_7e9a",
    "request body: SYNTHETIC_PRIVATE_INPUT_7e9a",
    "WORKFLOW_TIMEOUT echoed SYNTHETIC_PRIVATE_INPUT_7e9a",
    "WORKFLOW_CANCELLED echoed SYNTHETIC_PRIVATE_INPUT_7e9a",
])
async def test_untrusted_errors_not_copied_to_alerts_or_logs(privacy_job, monkeypatch, error):
    wf, owner = privacy_job
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 0)

    class Failure:
        async def execute(self, session, actor, prompt):
            raise RuntimeError(error)

    with Session(engine) as session:
        result = await WorkflowEngine(Failure()).execute(session, wf, owner)
        execution_id = result.id
        assert result.status == "failed"
    with Session(engine) as fresh:
        alerts = fresh.exec(select(WorkflowExecutionAlert).where(
            col(WorkflowExecutionAlert.execution_id) == execution_id)).all()
        logs = fresh.exec(select(WorkflowExecutionLog).where(
            col(WorkflowExecutionLog.execution_id) == execution_id)).all()
        assert len(alerts) == 1 and len(logs) == 2
        assert alerts[0].reason == "retry_exhausted"
        assert alerts[0].error_message == "WORKFLOW_RETRY_EXHAUSTED"
        assert all("SYNTHETIC_" not in row.message for row in logs)
        assert all("SYNTHETIC_" not in (row.error_message or "") for row in alerts)
        assert len({row.trace_id for row in logs} | {row.trace_id for row in alerts}) == 1


def test_owner_alert_openapi_export_and_readme_match_runtime():
    from app.main import app
    root = Path(__file__).resolve().parents[1]
    exported = json.loads((root / "docs/swagger.json").read_text(encoding="utf-8"))
    runtime = app.openapi()
    path = "/api/v1/workflows/alerts"
    assert path in exported["paths"]
    assert exported["paths"][path]["get"] == runtime["paths"][path]["get"]
    schemas = exported["components"]["schemas"]
    assert schemas["WorkflowAlertOut"] == runtime["components"]["schemas"]["WorkflowAlertOut"]
    assert path in (root / "README.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_success_summary_does_not_report_a_failure_reason(privacy_job):
    wf, owner = privacy_job

    class Success:
        async def execute(self, session, actor, prompt):
            return "done"

    with Session(engine) as session:
        result = await WorkflowEngine(Success()).execute(session, wf, owner)
        execution_id = result.id
        assert result.status == "success"
    with Session(engine) as fresh:
        logs = fresh.exec(select(WorkflowExecutionLog).where(
            col(WorkflowExecutionLog.execution_id) == execution_id)).all()
        assert len(logs) == 2
        assert all("bridge_error" not in row.message for row in logs)
        assert all(row.level == "info" for row in logs)
