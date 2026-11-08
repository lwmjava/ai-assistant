"""受权执行日志查询：分页与数据层一致，越权不泄露，读取不改记录。"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.audit.models import WorkflowExecutionAlert, WorkflowExecutionLog
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role, create_access_token
from app.main import app
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution
from app.workflow.execution_log import list_logs


def _user(key: str, role: str = Role.MEMBER.value, *, active: bool = True) -> User:
    return User(
        id=f"log-{key}",
        tenant_id=f"log-tenant-{key}",
        username=f"log-{key}",
        hashed_password="",
        role=role,
        is_active=active,
    )


def _headers(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.tenant_id, user.role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def logs_api(persist_identity):
    previous = settings.WORKFLOW_ENABLED
    settings.WORKFLOW_ENABLED = True
    owner = persist_identity(_user("owner"))
    other = persist_identity(_user("other"))
    admin = persist_identity(_user("admin", Role.SYSTEM_ADMIN.value))
    disabled = persist_identity(_user("disabled", active=False))
    created_workflow_ids: list[str] = []

    def add_workflow(user: User, name: str = "visible job") -> Workflow:
        with Session(engine) as session:
            workflow = Workflow(
                owner_id=user.id,
                tenant_id=user.tenant_id,
                name=name,
                cron_expr="0 0 1 1 *",
                prompt_template="safe prompt",
            )
            session.add(workflow)
            session.commit()
            session.refresh(workflow)
            session.expunge(workflow)
        created_workflow_ids.append(workflow.id)
        return workflow

    client = TestClient(app)
    yield {
        "client": client,
        "owner": owner,
        "other": other,
        "admin": admin,
        "disabled": disabled,
        "add_workflow": add_workflow,
    }
    app.dependency_overrides.clear()
    settings.WORKFLOW_ENABLED = previous
    with Session(engine) as session:
        for workflow_id in created_workflow_ids:
            for model in (WorkflowExecutionAlert, WorkflowExecutionLog, WorkflowExecution):
                for row in session.exec(select(model).where(col(model.workflow_id) == workflow_id)).all():
                    session.delete(row)
            workflow = session.get(Workflow, workflow_id)
            if workflow is not None:
                session.delete(workflow)
        session.commit()


def _stamp(execution: WorkflowExecution, logs: list[tuple[str, str]]) -> str:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with Session(engine) as session:
        session.add(execution)
        session.commit()
        session.refresh(execution)
        execution_id = execution.id
        tenant_id = execution.tenant_id
        workflow_id = execution.workflow_id
        for index, (level, message) in enumerate(logs):
            session.add(WorkflowExecutionLog(
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                execution_id=execution_id,
                trace_id=f"trace-{execution_id}",
                level=level,
                message=message,
                created_at=base + timedelta(seconds=index),
            ))
        session.commit()
    return execution_id


def test_log_pages_match_existing_query_and_detail_is_chronological(logs_api):
    owner = logs_api["owner"]
    workflow = logs_api["add_workflow"](owner, "paged job")
    execution = WorkflowExecution(
        workflow_id=workflow.id,
        tenant_id=workflow.tenant_id,
        status="failed",
        input="token=SYNTHETIC_SECRET_VALUE",
        output="13800138000",
        error="bridge failed " + ("B" * 600),
        duration_ms=1250,
    )
    execution_id = _stamp(execution, [
        ("info", "started"),
        ("error", "token=SYNTHETIC_SECRET_VALUE"),
        ("info", "ended"),
    ])
    with Session(engine) as session:
        session.add(WorkflowExecutionAlert(
            tenant_id=workflow.tenant_id,
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            execution_id=execution_id,
            trace_id=f"trace-{execution_id}",
            reason="bridge_error",
            error_message="WORKFLOW_EXECUTION_FAILED",
            detail="工作流执行最终失败",
        ))
        session.commit()
        expected = [row.id for row in list_logs(
            session, tenant_id=workflow.tenant_id, workflow_id=workflow.id, limit=10,
        )]

    client = logs_api["client"]
    headers = _headers(owner)
    first = client.get(
        f"/api/v1/workflows/{workflow.id}/logs",
        params={"limit": 2, "offset": 0},
        headers=headers,
    )
    second = client.get(
        f"/api/v1/workflows/{workflow.id}/logs",
        params={"limit": 2, "offset": 2},
        headers=headers,
    )
    assert first.status_code == 200
    assert second.status_code == 200
    body = first.json()
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == expected[:2]
    assert [item["id"] for item in second.json()["items"]] == expected[2:]
    assert "SYNTHETIC_SECRET_VALUE" not in first.text

    detail = client.get(
        f"/api/v1/workflows/{workflow.id}/executions/{execution_id}/logs",
        params={"limit": 2, "offset": 0},
        headers=headers,
    )
    assert detail.status_code == 200
    payload = detail.json()
    assert [item["message"] for item in payload["logs"]] == ["started", "token=***"]
    assert payload["total"] == 3
    assert payload["duration_ms"] == 1250
    assert payload["step_name"] == "Prompt 执行"
    assert payload["failure_reason"] == "bridge_error"
    assert payload["input"] == "token=***"
    assert payload["error"].startswith("bridge failed ")
    assert len(payload["error"]) == 500
    assert payload["output"] == "***"
    assert "13800138000" not in detail.text
    assert "SYNTHETIC_SECRET_VALUE" not in detail.text


def test_cross_tenant_log_read_hides_existence(logs_api):
    owner = logs_api["owner"]
    workflow = logs_api["add_workflow"](owner, "private job")
    execution = WorkflowExecution(
        workflow_id=workflow.id, tenant_id=workflow.tenant_id, status="success", output="hidden-output",
    )
    execution_id = _stamp(execution, [("info", "hidden-log-line")])
    client = logs_api["client"]
    missing = client.get("/api/v1/workflows/missing-workflow/logs", headers=_headers(logs_api["other"]))
    foreign = client.get(f"/api/v1/workflows/{workflow.id}/logs", headers=_headers(logs_api["other"]))
    assert missing.status_code == foreign.status_code == 404
    assert missing.json() == foreign.json()
    assert "private job" not in foreign.text
    assert "hidden-log-line" not in foreign.text
    foreign_detail = client.get(
        f"/api/v1/workflows/{workflow.id}/executions/{execution_id}/logs",
        headers=_headers(logs_api["other"]),
    )
    missing_detail = client.get(
        f"/api/v1/workflows/{workflow.id}/executions/missing-execution/logs",
        headers=_headers(owner),
    )
    assert foreign_detail.status_code == 404
    assert foreign_detail.json()["detail"] == "工作流不存在或无权访问"
    assert missing_detail.status_code == 404
    assert missing_detail.json()["detail"] == "执行记录不存在或无权访问"
    assert "hidden-output" not in foreign_detail.text

    visible = client.get(
        f"/api/v1/workflows/{workflow.id}/logs", headers=_headers(logs_api["admin"]),
    )
    assert visible.status_code == 200
    assert visible.json()["total"] == 1


def test_disabled_reader_is_rejected_and_reads_do_not_mutate(logs_api):
    owner = logs_api["owner"]
    workflow = logs_api["add_workflow"](owner)
    execution = WorkflowExecution(
        workflow_id=workflow.id, tenant_id=workflow.tenant_id, status="success", output="stable",
    )
    execution_id = _stamp(execution, [("info", "only-once")])
    client = logs_api["client"]
    denied = client.get("/api/v1/workflows/execution-alerts", headers=_headers(logs_api["disabled"]))
    assert denied.status_code == 403

    with Session(engine) as session:
        before_logs = session.exec(
            select(WorkflowExecutionLog).where(col(WorkflowExecutionLog.workflow_id) == workflow.id)
        ).all()
        before_alerts = session.exec(
            select(WorkflowExecutionAlert).where(col(WorkflowExecutionAlert.workflow_id) == workflow.id)
        ).all()
        before_executions = {
            row.id: (row.status, row.output, row.updated_at)
            for row in session.exec(
                select(WorkflowExecution).where(col(WorkflowExecution.workflow_id) == workflow.id)
            ).all()
        }
    headers = _headers(owner)
    assert client.get(f"/api/v1/workflows/{workflow.id}/logs", headers=headers).status_code == 200
    assert client.get(
        f"/api/v1/workflows/{workflow.id}/executions/{execution_id}/logs", headers=headers,
    ).status_code == 200
    with Session(engine) as session:
        assert len(session.exec(
            select(WorkflowExecutionLog).where(col(WorkflowExecutionLog.workflow_id) == workflow.id)
        ).all()) == len(before_logs)
        assert len(session.exec(
            select(WorkflowExecutionAlert).where(col(WorkflowExecutionAlert.workflow_id) == workflow.id)
        ).all()) == len(before_alerts)
        after_executions = {
            row.id: (row.status, row.output, row.updated_at)
            for row in session.exec(
                select(WorkflowExecution).where(col(WorkflowExecution.workflow_id) == workflow.id)
            ).all()
        }
    assert after_executions == before_executions


def test_execution_failure_alerts_stay_separate_from_owner_alerts(logs_api):
    owner = logs_api["owner"]
    workflow = logs_api["add_workflow"](owner, "failed job")
    other_workflow = logs_api["add_workflow"](logs_api["other"], "other failed job")
    execution = WorkflowExecution(
        workflow_id=workflow.id, tenant_id=workflow.tenant_id, status="failed", error="WORKFLOW_TIMEOUT",
    )
    execution_id = _stamp(execution, [("error", "timed out")])
    with Session(engine) as session:
        session.add(WorkflowExecutionAlert(
            tenant_id=workflow.tenant_id,
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            execution_id=execution_id,
            trace_id=f"trace-{execution_id}",
            reason="timeout",
            error_message="api_key=SYNTHETIC_SECRET_VALUE",
            detail="执行超时",
        ))
        session.add(WorkflowExecutionAlert(
            tenant_id=other_workflow.tenant_id,
            workflow_id=other_workflow.id,
            workflow_name=other_workflow.name,
            execution_id="other-execution",
            trace_id="trace-other",
            reason="timeout",
            error_message="other tenant",
        ))
        session.commit()

    client = logs_api["client"]
    failure = client.get("/api/v1/workflows/execution-alerts", headers=_headers(owner))
    owner_alerts = client.get("/api/v1/workflows/alerts", headers=_headers(owner))
    assert failure.status_code == 200
    assert owner_alerts.status_code == 200
    assert owner_alerts.json() == []
    rows = failure.json()
    assert len(rows) == 1
    assert rows[0]["workflow_id"] == workflow.id
    assert rows[0]["reason"] == "timeout"
    assert rows[0]["error_message"] == "api_key=***"
    assert "other failed job" not in failure.text
    assert "SYNTHETIC_SECRET_VALUE" not in failure.text
