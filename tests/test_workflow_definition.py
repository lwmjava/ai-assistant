"""受控工作流定义：只保存已批准的 Prompt 步骤，运行图与执行记录一致。"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.audit.models import WorkflowExecutionAlert, WorkflowExecutionLog
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role, create_access_token
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution


def _user(key: str, role: str = Role.MEMBER.value, *, active: bool = True) -> User:
    return User(
        id=f"def-{key}",
        tenant_id=f"def-tenant-{key}",
        username=f"def-{key}",
        hashed_password="",
        role=role,
        is_active=active,
    )


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.tenant_id, user.role)}"}


def _prompt(template: str, variables: list[str], step_id: str = "prompt", step_type: str = "prompt") -> dict:
    return {"id": step_id, "type": step_type, "template": template, "variables": variables}


@pytest.fixture
def definition_api(persist_identity):
    previous = settings.WORKFLOW_ENABLED
    settings.WORKFLOW_ENABLED = True
    set_llm_provider_override(MockLLMProvider())
    owner = persist_identity(_user("owner"))
    other = persist_identity(_user("other"))
    viewer = persist_identity(_user("viewer", Role.SYSTEM_VIEWER.value))
    disabled = persist_identity(_user("disabled", active=False))
    client = TestClient(app)
    created: list[str] = []

    def create(user: User, template: str = "hello") -> str:
        response = client.post(
            "/api/v1/workflows",
            headers=_headers(user),
            json={"name": "job", "cron_expr": "0 0 1 1 *", "prompt_template": template},
        )
        assert response.status_code == 201
        workflow_id = response.json()["id"]
        created.append(workflow_id)
        return workflow_id

    yield {
        "client": client,
        "owner": owner,
        "other": other,
        "viewer": viewer,
        "disabled": disabled,
        "create": create,
    }
    app.dependency_overrides.clear()
    set_llm_provider_override(None)
    settings.WORKFLOW_ENABLED = previous
    with Session(engine) as session:
        for workflow_id in created:
            for model in (WorkflowExecutionAlert, WorkflowExecutionLog, WorkflowExecution):
                rows = session.exec(select(model).where(col(model.workflow_id) == workflow_id)).all()
                for row in rows:
                    session.delete(row)
            workflow = session.get(Workflow, workflow_id)
            if workflow is not None:
                session.delete(workflow)
        session.commit()


def test_definition_save_reloads_and_keeps_template_literal(definition_api):
    owner = definition_api["owner"]
    client = definition_api["client"]
    workflow_id = definition_api["create"](owner, "hello")
    current = client.get(f"/api/v1/workflows/{workflow_id}/definition", headers=_headers(owner))
    assert current.status_code == 200
    saved = client.put(
        f"/api/v1/workflows/{workflow_id}/definition",
        headers=_headers(owner),
        json={
            "revision": current.json()["revision"],
            "steps": [_prompt("hello {customer}", ["customer"])],
            "edges": [],
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["steps"] == [_prompt("hello {customer}", ["customer"])]
    assert body["edges"] == []
    assert body["revision"] != current.json()["revision"]
    reloaded = client.get(f"/api/v1/workflows/{workflow_id}/definition", headers=_headers(owner))
    assert reloaded.json()["steps"] == body["steps"]
    run = client.post(f"/api/v1/workflows/{workflow_id}/run", headers=_headers(owner))
    assert run.status_code == 201
    assert run.json()["input"] == "hello {customer}"
    assert run.json()["status"] == "success"


def test_unknown_step_invalid_variable_and_cycle_do_not_save(definition_api):
    owner = definition_api["owner"]
    client = definition_api["client"]
    workflow_id = definition_api["create"](owner, "keep me")
    revision = client.get(f"/api/v1/workflows/{workflow_id}/definition", headers=_headers(owner)).json()["revision"]
    cases = [
        ({"steps": [_prompt("print(1)", [], "tool", "python")], "edges": []}, "unknown_step"),
        ({"steps": [_prompt("hello {1bad}", ["1bad"])], "edges": []}, "invalid_variable"),
        ({"steps": [_prompt("hello {name}", [])], "edges": []}, "invalid_variable"),
        (
            {
                "steps": [_prompt("hello", [])],
                "edges": [{"source": "prompt", "target": "prompt"}],
            },
            "cycle",
        ),
    ]
    for payload, code in cases:
        response = client.put(
            f"/api/v1/workflows/{workflow_id}/definition",
            headers=_headers(owner),
            json={"revision": revision, **payload},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == code
    kept = client.get(f"/api/v1/workflows/{workflow_id}/definition", headers=_headers(owner))
    assert kept.json()["steps"][0]["template"] == "keep me"
    assert kept.json()["revision"] == revision


def test_stale_revision_conflicts_and_hides_other_tenants(definition_api):
    owner = definition_api["owner"]
    client = definition_api["client"]
    workflow_id = definition_api["create"](owner, "original")
    current = client.get(f"/api/v1/workflows/{workflow_id}/definition", headers=_headers(owner)).json()
    renamed = client.put(
        f"/api/v1/workflows/{workflow_id}",
        headers=_headers(owner),
        json={"name": "renamed"},
    )
    assert renamed.status_code == 200
    stale = client.put(
        f"/api/v1/workflows/{workflow_id}/definition",
        headers=_headers(owner),
        json={"revision": current["revision"], "steps": [_prompt("changed", [])], "edges": []},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "revision_conflict"
    kept = client.get(f"/api/v1/workflows/{workflow_id}/definition", headers=_headers(owner))
    assert kept.json()["steps"][0]["template"] == "original"

    missing = client.get("/api/v1/workflows/missing-workflow/definition", headers=_headers(definition_api["other"]))
    foreign = client.get(f"/api/v1/workflows/{workflow_id}/definition", headers=_headers(definition_api["other"]))
    assert missing.status_code == foreign.status_code == 404
    assert missing.json() == foreign.json()
    assert "original" not in foreign.text
    denied = client.put(
        f"/api/v1/workflows/{workflow_id}/definition",
        headers=_headers(definition_api["viewer"]),
        json={"revision": kept.json()["revision"], "steps": [_prompt("changed", [])], "edges": []},
    )
    assert denied.status_code == 403
    created = client.post(
        "/api/v1/workflows",
        headers=_headers(owner),
        json={"name": "bad", "cron_expr": "0 0 1 1 *", "prompt_template": "hello {1bad}"},
    )
    assert created.status_code == 422
    assert created.json()["detail"] == "invalid_variable"
    assert client.get(
        f"/api/v1/workflows/{workflow_id}/definition", headers=_headers(owner),
    ).json()["steps"][0]["template"] == "original"


def test_disabled_reader_and_execution_graph_match_record(definition_api):
    owner = definition_api["owner"]
    client = definition_api["client"]
    workflow_id = definition_api["create"](owner, "hello")
    assert client.get(
        f"/api/v1/workflows/{workflow_id}/definition",
        headers=_headers(definition_api["disabled"]),
    ).status_code == 403
    execution = WorkflowExecution(
        workflow_id=workflow_id,
        tenant_id=owner.tenant_id,
        status="failed",
        input="hello",
        error="WORKFLOW_TIMEOUT",
        duration_ms=40,
    )
    with Session(engine) as session:
        session.add(execution)
        session.commit()
        session.refresh(execution)
        execution_id = execution.id
        base = datetime(2026, 3, 1, tzinfo=UTC)
        session.add(WorkflowExecutionLog(
            tenant_id=owner.tenant_id,
            workflow_id=workflow_id,
            execution_id=execution_id,
            trace_id="trace-def",
            level="info",
            message="执行开始",
            created_at=base,
        ))
        session.add(WorkflowExecutionLog(
            tenant_id=owner.tenant_id,
            workflow_id=workflow_id,
            execution_id=execution_id,
            trace_id="trace-def",
            level="error",
            message="执行结束 status=failed reason=timeout",
            created_at=base + timedelta(seconds=1),
        ))
        session.add(WorkflowExecutionAlert(
            tenant_id=owner.tenant_id,
            workflow_id=workflow_id,
            workflow_name="job",
            execution_id=execution_id,
            trace_id="trace-def",
            reason="timeout",
            error_message="WORKFLOW_TIMEOUT",
        ))
        session.commit()
        stored = session.get(WorkflowExecution, execution_id)
        assert stored is not None
        before = (stored.status, stored.updated_at)

    graph = client.get(
        f"/api/v1/workflows/{workflow_id}/executions/{execution_id}/graph",
        headers=_headers(owner),
    )
    assert graph.status_code == 200
    body = graph.json()
    assert body["status"] == "failed"
    assert body["nodes"] == [{
        "id": "prompt",
        "type": "prompt",
        "status": "failed",
        "failed": True,
        "reason": "timeout",
    }]
    assert [item["message"] for item in body["trajectory"]] == [
        "执行开始",
        "执行结束 status=failed reason=timeout",
    ]
    assert {item["node_id"] for item in body["trajectory"]} == {"prompt"}
    with Session(engine) as session:
        stored = session.get(WorkflowExecution, execution_id)
        assert stored is not None
        assert (stored.status, stored.updated_at) == before
