"""Doc-assisted evaluation question candidates: binding, budget, Silver entry."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.models.evaluation import EvaluationDatasetVersion
from app.models.rag import Document, DocumentChunk
from app.models.user import Tenant, User
from app.services.evaluation_datasets import DatasetValidationError, EvaluationDatasetService


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
            User(id="admin-b", tenant_id="t-b", username="admin-b", hashed_password="", role="tenant_admin")
        )
        session.add(
            Document(
                id="doc-a",
                tenant_id="t-a",
                user_id="admin-a",
                title="授权退货政策",
                content_hash="hash-a",
                version_group_id="vg-a",
                version_number=1,
                is_current=True,
                version_state="published",
                chunk_count=1,
            )
        )
        session.add(
            DocumentChunk(
                id="chunk-a",
                tenant_id="t-a",
                document_id="doc-a",
                content=(
                    "七日内可退货，需保留完整包装。联系邮箱 support@example.com "
                    "或电话 13800138000。密钥 sk_test_abcdefghijklmnopqrstuvwxyz12。"
                ),
                chunk_index=0,
            )
        )
        session.add(
            Document(
                id="doc-b",
                tenant_id="t-b",
                user_id="admin-b",
                title="外租户文档",
                content_hash="hash-b",
                version_group_id="vg-b",
                version_number=1,
                is_current=True,
                version_state="published",
                chunk_count=1,
            )
        )
        session.add(
            DocumentChunk(
                id="chunk-b",
                tenant_id="t-b",
                document_id="doc-b",
                content="外租户机密内容不可用于跨租户出题。",
                chunk_index=0,
            )
        )
        session.commit()

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = database
    headers = {
        "admin": {"Authorization": f"Bearer {create_access_token('admin-a', 't-a', 'system_admin')}"},
        "other": {"Authorization": f"Bearer {create_access_token('admin-b', 't-b', 'tenant_admin')}"},
    }
    yield TestClient(app), engine, headers
    app.dependency_overrides.pop(get_session, None)
    engine.dispose()


def _create_dataset(client: TestClient, headers: dict) -> str:
    created = client.post(
        "/api/v1/evaluations/datasets",
        headers=headers["admin"],
        json={"name": "文档辅助题", "slug": "doc-assisted", "family": "retrieval"},
    )
    assert created.status_code == 200, created.text
    return created.json()["id"]


def test_source_binding_redaction_and_fake_budget(ctx):
    client, _, headers = ctx
    dataset_id = _create_dataset(client, headers)
    generated = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/generate",
        headers=headers["admin"],
        json={
            "document_ids": ["doc-a"],
            "idempotency_key": "idem-1",
            "max_cases": 5,
            "max_external_calls": 0,
            "data_egress_confirmed": False,
            "provider": "fake",
        },
    )
    assert generated.status_code == 200, generated.text
    body = generated.json()
    assert body["version"]["source"] == "ai_generated"
    assert body["version"]["status"] == "draft"
    assert body["index"]["external_calls_used"] == 0
    assert body["index"]["generation_config"]["provider"] == "fake"
    case = body["cases"][0]
    assert case["tier"] == "silver"
    assert case["candidate_status"] == "pending"
    assert case["provenance"]["source_document_id"] == "doc-a"
    assert case["provenance"]["source_version_number"] == 1
    assert case["provenance"]["source_chunk_id"] == "chunk-a"
    assert "[REDACTED_EMAIL]" in case["expected_evidence"]
    assert "[REDACTED_PHONE]" in case["expected_evidence"]
    assert "[REDACTED_SECRET]" in case["expected_evidence"]
    assert "support@example.com" not in case["expected_evidence"]
    assert "sk_test_" not in json.dumps(case, ensure_ascii=False)

    replay = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/generate",
        headers=headers["admin"],
        json={
            "document_ids": ["doc-a"],
            "idempotency_key": "idem-1",
            "provider": "fake",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["version"]["id"] == body["version"]["id"]


def test_live_provider_and_budget_guards(ctx):
    client, engine, headers = ctx
    dataset_id = _create_dataset(client, headers)
    denied = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/generate",
        headers=headers["admin"],
        json={
            "document_ids": ["doc-a"],
            "idempotency_key": "live-1",
            "provider": "live",
            "max_external_calls": 0,
            "data_egress_confirmed": True,
        },
    )
    assert denied.status_code == 422
    denied2 = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/generate",
        headers=headers["admin"],
        json={
            "document_ids": ["doc-a"],
            "idempotency_key": "live-2",
            "provider": "live",
            "max_external_calls": 3,
            "data_egress_confirmed": True,
        },
    )
    assert denied2.status_code == 422

    with Session(engine) as session:
        user = session.get(User, "admin-a")
        service = EvaluationDatasetService(session)

        def greedy(**_kwargs):
            return (
                [
                    {
                        "case_id": "overflow-1",
                        "split": "development",
                        "tier": "silver",
                        "query": "超预算问题示例文本",
                        "expected_document_ids": ["doc-a"],
                        "expected_evidence": "超预算证据文本足够长",
                        "candidate_status": "pending",
                        "unavailable_reason": None,
                        "provenance": {
                            "source_document_id": "doc-a",
                            "source_version_number": 1,
                            "source_content_hash": "hash-a",
                            "source_chunk_id": "chunk-a",
                            "fragment": "超预算证据文本足够长",
                            "generator": "greedy",
                            "prompt_version": "x",
                            "generation_config": {},
                            "generated_at": "2026-11-10T00:00:00+00:00",
                        },
                    }
                ],
                5,
            )

        with pytest.raises(DatasetValidationError):
            service.generate_doc_questions(
                user,
                dataset_id,
                document_ids=["doc-a"],
                idempotency_key="budget-boom",
                max_external_calls=2,
                data_egress_confirmed=True,
                provider="live",
                generator=greedy,
            )


def test_cross_tenant_document_rejected(ctx):
    client, _, headers = ctx
    dataset_id = _create_dataset(client, headers)
    response = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/generate",
        headers=headers["admin"],
        json={"document_ids": ["doc-b"], "idempotency_key": "x-tenant", "provider": "fake"},
    )
    assert response.status_code == 404


def test_edit_reject_reopen_promote_and_no_auto_gold(ctx):
    client, engine, headers = ctx
    dataset_id = _create_dataset(client, headers)
    generated = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/generate",
        headers=headers["admin"],
        json={"document_ids": ["doc-a"], "idempotency_key": "flow-1", "provider": "fake"},
    ).json()
    version_id = generated["version"]["id"]
    case_id = generated["cases"][0]["case_id"]

    edited = client.patch(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/{version_id}/cases/{case_id}",
        headers=headers["admin"],
        json={"query": "编辑后的授权资料问题，要求可检索", "expected_answer_points": ["七日内可退货"]},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["cases"][0]["candidate_status"] == "edited"
    assert edited.json()["cases"][0]["query"].startswith("编辑后的")

    rejected = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/{version_id}/cases/{case_id}/review",
        headers=headers["admin"],
        json={"decision": "reject", "notes": "暂不可用"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["cases"][0]["candidate_status"] == "rejected"

    # 拒绝可撤销
    reopened = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/{version_id}/cases/{case_id}/review",
        headers=headers["admin"],
        json={"decision": "reopen"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["cases"][0]["candidate_status"] == "pending"

    accepted = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/{version_id}/cases/{case_id}/review",
        headers=headers["admin"],
        json={"decision": "accept"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["cases"][0]["candidate_status"] == "accepted"

    empty_promote = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/{version_id}/promote",
        headers=headers["admin"],
        json={"silver_version": "silver-empty"},
    )
    # still accepted so should succeed
    assert empty_promote.status_code == 200, empty_promote.text
    silver = empty_promote.json()
    assert silver["source"] == "ai_generated"
    assert silver["status"] == "draft"
    detail = client.get(
        f"/api/v1/evaluations/datasets/{dataset_id}/versions/{silver['id']}",
        headers=headers["admin"],
    )
    assert detail.status_code == 200
    cases = detail.json()["cases"]
    assert cases and all(case["tier"] == "silver" for case in cases)
    assert all(case.get("split") != "holdout" for case in cases)

    with Session(engine) as session:
        row = session.exec(
            select(EvaluationDatasetVersion).where(EvaluationDatasetVersion.id == silver["id"])
        ).one()
        index = json.loads(row.index_json or "{}")
        assert index.get("auto_gold") is False
        assert index.get("human_accepted") is True


def test_revoked_source_makes_candidate_unavailable_and_blocks_promote(ctx):
    client, engine, headers = ctx
    dataset_id = _create_dataset(client, headers)
    generated = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/generate",
        headers=headers["admin"],
        json={"document_ids": ["doc-a"], "idempotency_key": "revoke-1", "provider": "fake"},
    ).json()
    version_id = generated["version"]["id"]
    case_id = generated["cases"][0]["case_id"]
    assert (
        client.post(
            f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/{version_id}/cases/{case_id}/review",
            headers=headers["admin"],
            json={"decision": "accept"},
        ).status_code
        == 200
    )

    with Session(engine) as session:
        document = session.get(Document, "doc-a")
        document.version_state = "archived"
        document.is_current = False
        session.add(document)
        session.commit()

    listed = client.get(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/{version_id}",
        headers=headers["admin"],
    )
    assert listed.status_code == 200
    case = listed.json()["cases"][0]
    assert case["unavailable_reason"] in {"source_invalid", "source_revoked"}
    assert case["candidate_status"] == "accepted"

    blocked = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/doc-questions/{version_id}/promote",
        headers=headers["admin"],
        json={"silver_version": "silver-blocked"},
    )
    assert blocked.status_code == 422
