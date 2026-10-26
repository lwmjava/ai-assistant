"""Real identity dependencies guard report metadata and content alike."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.models.user import Tenant, User


@pytest.fixture
def identity():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="eval-a", name="Evaluation tenant"))
        session.add(User(id="eval-user", tenant_id="eval-a", username="evaluation-reader",
                         hashed_password="", role="system_admin"))
        session.commit()

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = database
    token = create_access_token("eval-user", "eval-a", "system_admin")
    yield TestClient(app), engine, {"Authorization": f"Bearer {token}"}
    app.dependency_overrides.pop(get_session, None)
    engine.dispose()


def test_api_requires_authentication(identity):
    client, _, _ = identity
    for path in [
        "/reports",
        "/reports/retrieval-20260919",
        "/trend?family=agent&metric=route_accuracy",
        "/retrieval/strategies",
    ]:
        assert client.get("/api/v1/evaluations" + path).status_code == 401


def test_api_uses_current_role_not_token_claims(identity):
    client, engine, headers = identity
    assert client.get("/api/v1/evaluations/reports", headers=headers).json()["total"] == 4
    with Session(engine) as session:
        user = session.get(User, "eval-user")
        user.role = "member"
        session.add(user)
        session.commit()
    assert client.get("/api/v1/evaluations/reports", headers=headers).status_code == 403


@pytest.mark.parametrize("subject", ["user", "tenant", "deleted"])
def test_disabled_or_removed_identity_is_rejected(identity, subject):
    client, engine, headers = identity
    with Session(engine) as session:
        user = session.get(User, "eval-user")
        user.role = "tenant_admin"
        session.add(user)
        if subject == "deleted":
            session.delete(user)
        elif subject == "user":
            user.is_active = False
        else:
            tenant = session.get(Tenant, "eval-a")
            tenant.is_active = False
            session.add(tenant)
        session.commit()
    assert client.get("/api/v1/evaluations/reports", headers=headers).status_code in (401, 403)


def test_tenant_cannot_discover_global_reports(identity):
    client, engine, headers = identity
    with Session(engine) as session:
        user = session.get(User, "eval-user")
        user.role = "tenant_admin"
        session.add(user)
        session.commit()
    assert client.get("/api/v1/evaluations/reports", headers=headers).status_code == 403
    known = client.get("/api/v1/evaluations/reports/retrieval-20260919", headers=headers)
    unknown = client.get("/api/v1/evaluations/reports/absent", headers=headers)
    assert known.status_code == unknown.status_code == 403
    assert known.json() == unknown.json()


def test_contract_and_parameter_validation(identity):
    client, _, headers = identity
    detail = client.get("/api/v1/evaluations/reports/generation-20261009", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "failed"
    assert "query" not in detail.json()
    for query in ["page=0", "family=unknown", "page_size=101"]:
        assert client.get(f"/api/v1/evaluations/reports?{query}", headers=headers).status_code == 422
    trend = client.get("/api/v1/evaluations/trend?family=agent&metric=tool_call_validity_recall", headers=headers)
    assert trend.status_code == 200
    assert trend.json()["points"][0]["value"] is None
    assert client.get("/api/v1/evaluations/trend?family=agent&metric=query", headers=headers).status_code == 422
    strategies = client.get("/api/v1/evaluations/retrieval/strategies", headers=headers)
    assert strategies.status_code == 200
    assert "native" in strategies.json()["backends"]


@pytest.mark.parametrize(("code", "message"), [
    ("quality_disabled", "尚未启用"),
    ("quality_pricing_unavailable", "生成模型服务或价格配置"),
    ("quality_embedding_pricing_unavailable", "Embedding 服务或价格配置"),
    ("quality_capability_unavailable", "已登记能力"),
    ("quality_call_budget_insufficient", "启用 Judge 时至少 3 次"),
    ("quality_case_limit", "1 至 10 条"),
    ("quality_budget_invalid", "不超过 5 元"),
    ("quality_data_confirmation_required", "合成或已脱敏"),
    ("judge_requires_generation", "须启用 RAG 生成"),
    ("sensitive-unknown-provider-error", "请求不符合评测任务策略"),
])
def test_job_policy_errors_explain_safe_recovery_without_raw_errors(identity, monkeypatch, code, message):
    from app.services.evaluation_jobs import EvaluationJobService, EvaluationJobValidationError

    def reject(self, user, **kwargs):
        raise EvaluationJobValidationError(code)

    monkeypatch.setattr(EvaluationJobService, "create_real", reject)
    client, _, headers = identity
    response = client.post("/api/v1/evaluations/jobs", headers=headers, json={
        "tenant_id": "eval-a", "target_type": "rag", "target_ids": ["synthetic-doc"],
        "enable_generation": True, "enable_judge": True,
        "max_external_calls": 10, "max_cost_yuan": 0.1,
        "data_egress_confirmed": True, "idempotency_key": "policy-error-regression",
    })
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert code not in response.json()["detail"]
