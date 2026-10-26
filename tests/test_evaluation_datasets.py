"""EVAL-002 dataset versioning, review policy, and feedback candidates."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.models.feedback import ChatFeedback
from app.models.user import Tenant, User
from app.services.evaluation_datasets import canonical_cases_hash

CASES = [
    {
        "case_id": "rag-101",
        "split": "development",
        "tier": "silver",
        "query": "退货政策是什么",
    },
    {
        "case_id": "rag-102",
        "split": "holdout",
        "tier": "silver",
        "query": "holdout 专用问题",
        "expected_answer_points": ["秘密答案"],
    },
]


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="t-a", name="A"))
        session.add(Tenant(id="t-b", name="B"))
        session.add(
            User(id="admin-a", tenant_id="t-a", username="admin-a", hashed_password="", role="system_admin")
        )
        session.add(
            User(id="viewer-a", tenant_id="t-a", username="viewer-a", hashed_password="", role="system_viewer")
        )
        session.add(
            User(id="sys", tenant_id="t-a", username="sys", hashed_password="", role="system_admin")
        )
        session.add(
            User(id="admin-b", tenant_id="t-b", username="admin-b", hashed_password="", role="tenant_admin")
        )
        session.add(
            ChatFeedback(
                id="fb-1",
                tenant_id="t-a",
                user_id="member-x",
                attempt_id="attempt-1",
                conversation_id="conv-1",
                kind="api_error",
                comment="已脱敏评论",
            )
        )
        session.commit()

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = database
    headers = {
        "admin": {"Authorization": f"Bearer {create_access_token('admin-a', 't-a', 'system_admin')}"},
        "viewer": {"Authorization": f"Bearer {create_access_token('viewer-a', 't-a', 'system_viewer')}"},
        "sys": {"Authorization": f"Bearer {create_access_token('sys', 't-a', 'system_admin')}"},
        "other": {"Authorization": f"Bearer {create_access_token('admin-b', 't-b', 'tenant_admin')}"},
    }
    yield TestClient(app), engine, headers
    app.dependency_overrides.pop(get_session, None)
    engine.dispose()


def test_import_export_hash_and_platform_admin_detail(ctx):
    client, _, headers = ctx
    created = client.post(
        "/api/v1/evaluations/datasets",
        headers=headers["admin"],
        json={"name": "回归集", "slug": "regression-a", "family": "retrieval"},
    )
    assert created.status_code == 200, created.text
    dataset_id = created.json()["id"]
    imported = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/versions",
        headers=headers["admin"],
        json={"version": "0.1.0", "cases": CASES, "source": "upload"},
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["content_hash"] == canonical_cases_hash(CASES)
    assert body["case_count"] == 2
    exported = client.get(
        f"/api/v1/evaluations/datasets/{dataset_id}/versions/{body['id']}",
        headers=headers["admin"],
    )
    assert exported.status_code == 200
    holdout = next(c for c in exported.json()["cases"] if c["case_id"] == "rag-102")
    assert holdout["expected_answer_points"] == ["秘密答案"]
    full = client.get(
        f"/api/v1/evaluations/datasets/{dataset_id}/versions/{body['id']}",
        headers=headers["sys"],
    )
    assert next(c for c in full.json()["cases"] if c["case_id"] == "rag-102")["expected_answer_points"] == [
        "秘密答案"
    ]


def test_ai_generated_cannot_be_gold_and_frozen_immutable(ctx):
    client, _, headers = ctx
    dataset_id = client.post(
        "/api/v1/evaluations/datasets",
        headers=headers["admin"],
        json={"name": "AI", "slug": "ai-set", "family": "retrieval"},
    ).json()["id"]
    bad = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/versions",
        headers=headers["admin"],
        json={
            "version": "0.0.1",
            "source": "ai_generated",
            "cases": [{**CASES[0], "tier": "gold"}],
        },
    )
    assert bad.status_code == 422
    frozen = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/versions",
        headers=headers["admin"],
        json={"version": "0.0.2", "cases": CASES, "is_frozen": True},
    )
    assert frozen.status_code == 200
    assert (
        client.post(
            f"/api/v1/evaluations/datasets/{dataset_id}/versions/{frozen.json()['id']}/submit",
            headers=headers["sys"],
        ).status_code
        == 422
    )


def test_review_gold_requires_system_admin_accept(ctx):
    client, _, headers = ctx
    dataset_id = client.post(
        "/api/v1/evaluations/datasets",
        headers=headers["admin"],
        json={"name": "Gold", "slug": "gold-set", "family": "retrieval"},
    ).json()["id"]
    version = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/versions",
        headers=headers["admin"],
        json={
            "version": "1.0.0",
            "cases": [{**CASES[0], "tier": "gold"}],
        },
    ).json()
    assert (
        client.post(
            f"/api/v1/evaluations/datasets/{dataset_id}/versions/{version['id']}/submit",
            headers=headers["admin"],
        ).status_code
        == 200
    )
    denied = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/versions/{version['id']}/review",
        headers=headers["admin"],
        json={"decision": "approve"},
    )
    assert denied.status_code == 422
    approved = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/versions/{version['id']}/review",
        headers=headers["sys"],
        json={"decision": "approve", "notes": "ACCEPT_GOLD"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["review_decision"] == "ACCEPT_GOLD"
    assert (
        client.delete(
            f"/api/v1/evaluations/datasets/{dataset_id}/versions/{version['id']}",
            headers=headers["sys"],
        ).status_code
        == 422
    )


def test_tenant_isolation_and_feedback_candidate_audit(ctx):
    client, _, headers = ctx
    dataset_id = client.post(
        "/api/v1/evaluations/datasets",
        headers=headers["admin"],
        json={"name": "隔离", "slug": "iso", "family": "agent"},
    ).json()["id"]
    assert client.get(f"/api/v1/evaluations/datasets/{dataset_id}", headers=headers["other"]).status_code == 403
    assert client.get("/api/v1/evaluations/datasets", headers=headers["viewer"]).status_code == 403
    assert (
        client.post(
            "/api/v1/evaluations/datasets",
            headers=headers["viewer"],
            json={"name": "x", "slug": "x", "family": "agent"},
        ).status_code
        == 403
    )
    nominated = client.post(
        "/api/v1/evaluations/candidates",
        headers=headers["admin"],
        json={"source_type": "feedback", "source_id": "fb-1", "target_dataset_id": dataset_id},
    )
    assert nominated.status_code == 200, nominated.text
    payload = nominated.json()
    assert payload["status"] == "pending"
    assert payload["snapshot"]["kind"] == "api_error"
    assert payload["source_id"] == "fb-1"
    reviewed = client.post(
        f"/api/v1/evaluations/candidates/{payload['id']}/review",
        headers=headers["admin"],
        json={"decision": "accept", "notes": "进入待审回归池"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "accepted"
    assert reviewed.json()["reviewed_by"] == "admin-a"
    assert (
        client.post(
            "/api/v1/evaluations/candidates",
            headers=headers["other"],
            json={"source_type": "feedback", "source_id": "fb-1"},
        ).status_code
        == 403
    )


def test_invalid_schema_rejected(ctx):
    client, _, headers = ctx
    dataset_id = client.post(
        "/api/v1/evaluations/datasets",
        headers=headers["admin"],
        json={"name": "坏数据", "slug": "bad", "family": "retrieval"},
    ).json()["id"]
    for cases in (
        [],
        [{"case_id": "x", "split": "train", "tier": "silver", "query": "足够长的问题"}],
        [{"case_id": "x", "split": "development", "tier": "platinum", "query": "足够长的问题"}],
    ):
        assert (
            client.post(
                f"/api/v1/evaluations/datasets/{dataset_id}/versions",
                headers=headers["admin"],
                json={"version": "bad-1", "cases": cases},
            ).status_code
            == 422
        )
