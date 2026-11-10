"""Prompt variant offline compare, authorized publish, and rollback."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.models.evaluation import EvaluationDataset, EvaluationDatasetVersion
from app.models.prompt_variant import PromptPublication
from app.models.user import Tenant, User
from app.services.prompt_variants import PromptVariantService

CASES = [
    {"case_id": "p-1", "split": "development", "tier": "silver", "query": "你好，请介绍一下"},
    {"case_id": "p-2", "split": "development", "tier": "silver", "query": "退货政策"},
    {"case_id": "p-3", "split": "development", "tier": "silver", "query": "发票怎么开"},
]

SLOT = "SYSTEM_UNDERSTAND"
API = "/api/v1/evaluations/prompt-variants"


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="t-a", name="A"))
        session.add(Tenant(id="t-b", name="B"))
        session.add(
            User(
                id="admin-a",
                tenant_id="t-a",
                username="admin-a",
                hashed_password="",
                role="system_admin",
            )
        )
        session.add(
            User(
                id="member-a",
                tenant_id="t-a",
                username="member-a",
                hashed_password="",
                role="member",
            )
        )
        session.add(
            User(
                id="admin-b",
                tenant_id="t-b",
                username="admin-b",
                hashed_password="",
                role="system_admin",
            )
        )
        session.commit()

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = database
    headers = {
        "admin": {
            "Authorization": f"Bearer {create_access_token('admin-a', 't-a', 'system_admin')}"
        },
        "member": {
            "Authorization": f"Bearer {create_access_token('member-a', 't-a', 'member')}"
        },
        "other": {
            "Authorization": f"Bearer {create_access_token('admin-b', 't-b', 'system_admin')}"
        },
    }
    yield TestClient(app), engine, headers
    app.dependency_overrides.pop(get_session, None)
    engine.dispose()


def _dataset(client: TestClient, headers: dict, *, cases: list[dict] | None = None) -> tuple[str, str]:
    created = client.post(
        "/api/v1/evaluations/datasets",
        headers=headers["admin"],
        json={"name": "Prompt比较集", "slug": f"prompt-compare-{uuid.uuid4().hex[:8]}", "family": "retrieval"},
    )
    assert created.status_code == 200, created.text
    dataset_id = created.json()["id"]
    imported = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/versions",
        headers=headers["admin"],
        json={"version": "0.1.0", "cases": cases if cases is not None else CASES, "source": "upload"},
    )
    assert imported.status_code == 200, imported.text
    return dataset_id, imported.json()["id"]


def _variant(client: TestClient, headers: dict, label: str, body: str) -> dict:
    created = client.post(
        f"{API}/slots/{SLOT}/variants",
        headers=headers["admin"],
        json={"label": label, "body": body},
    )
    assert created.status_code == 200, created.text
    return created.json()


def test_slots_and_variant_crud(ctx):
    client, _, headers = ctx
    slots = client.get(f"{API}/slots", headers=headers["admin"])
    assert slots.status_code == 200
    keys = {item["key"] for item in slots.json()}
    assert SLOT in keys
    assert all("source_fingerprint" in item for item in slots.json())
    variant = _variant(client, headers, "baseline", "你是严谨的意图分析器。")
    listed = client.get(f"{API}/slots/{SLOT}/variants", headers=headers["admin"])
    assert listed.status_code == 200
    assert any(item["id"] == variant["id"] for item in listed.json())


def test_compare_binds_versions_and_keeps_missing_metrics(ctx):
    client, engine, headers = ctx
    dataset_id, version_id = _dataset(client, headers)
    baseline = _variant(client, headers, "base", "基线提示词 A")
    candidate = _variant(client, headers, "cand", "候选提示词 B，增加约束。")
    compared = client.post(
        f"{API}/compares",
        headers=headers["admin"],
        json={
            "slot_key": SLOT,
            "baseline_variant_id": baseline["id"],
            "candidate_variant_id": candidate["id"],
            "dataset_id": dataset_id,
            "dataset_version_id": version_id,
            "provider": "fake",
            "max_external_calls": 0,
        },
    )
    assert compared.status_code == 200, compared.text
    body = compared.json()
    binding = body["binding"]
    assert binding["dataset_id"] == dataset_id
    assert binding["dataset_version_id"] == version_id
    assert binding["source_version"]
    assert binding["model_version"]
    assert binding["evaluation_version"]
    assert binding["baseline_fingerprint"] == baseline["body_fingerprint"]
    assert binding["candidate_fingerprint"] == candidate["body_fingerprint"]
    assert body["provider"] == "fake"
    assert body["external_calls_used"] == 0
    assert body["auto_publish_blocked"] is True
    assert body["diff"]["auto_publish"] is False
    assert "pass_rate" in body["baseline_metrics"]
    assert body["baseline_metrics"]["pass_rate"] is not None
    assert "human_acceptance_rate" in body["missing_metrics"]
    assert "holdout_score" in body["missing_metrics"]
    assert body["baseline_metrics"].get("human_acceptance_rate") is None
    assert body["cost"]["total_amount"] == 0.0
    assert "explain" in body["call_stats"]
    assert body["call_stats"]["baseline"]["external"] == 0
    assert body["call_stats"]["baseline"]["cache_hits"] + body["call_stats"]["baseline"][
        "cache_misses"
    ] == body["call_stats"]["baseline"]["total"]

    with Session(engine) as session:
        empty_dataset = EvaluationDataset(
            tenant_id="t-a",
            name="空集",
            slug=f"prompt-empty-{uuid.uuid4().hex[:8]}",
            family="retrieval",
            created_by="admin-a",
        )
        session.add(empty_dataset)
        session.flush()
        empty_version = EvaluationDatasetVersion(
            dataset_id=empty_dataset.id,
            tenant_id="t-a",
            version="0.1.0",
            status="draft",
            source="upload",
            content_hash="empty",
            cases_json="[]",
            case_count=0,
            created_by="admin-a",
        )
        session.add(empty_version)
        session.commit()
        empty_dataset_id = empty_dataset.id
        empty_version_id = empty_version.id
    incomplete = client.post(
        f"{API}/compares",
        headers=headers["admin"],
        json={
            "slot_key": SLOT,
            "baseline_variant_id": baseline["id"],
            "candidate_variant_id": candidate["id"],
            "dataset_id": empty_dataset_id,
            "dataset_version_id": empty_version_id,
            "provider": "fake",
            "max_external_calls": 0,
        },
    )
    assert incomplete.status_code == 200, incomplete.text
    empty_body = incomplete.json()
    assert empty_body["status"] == "incomplete"
    assert empty_body["baseline_metrics"]["pass_rate"] is None
    assert "pass_rate" in empty_body["missing_metrics"]
    assert empty_body["baseline_metrics"]["pass_rate"] is None


def test_paid_provider_rejected(ctx):
    client, _, headers = ctx
    dataset_id, version_id = _dataset(client, headers)
    baseline = _variant(client, headers, "base-paid", "基线")
    candidate = _variant(client, headers, "cand-paid", "候选")
    # schema Literal blocks non-fake at validation layer
    rejected = client.post(
        f"{API}/compares",
        headers=headers["admin"],
        json={
            "slot_key": SLOT,
            "baseline_variant_id": baseline["id"],
            "candidate_variant_id": candidate["id"],
            "dataset_id": dataset_id,
            "dataset_version_id": version_id,
            "provider": "live",
            "max_external_calls": 1,
        },
    )
    assert rejected.status_code == 422


def test_unauthorized_and_concurrent_publish_and_rollback(ctx):
    client, engine, headers = ctx
    baseline = _variant(client, headers, "pub-base", "发布基线提示词")
    candidate = _variant(client, headers, "pub-cand", "发布候选提示词")

    forbidden = client.post(
        f"{API}/slots/{SLOT}/publish",
        headers=headers["member"],
        json={"variant_id": baseline["id"], "expected_revision": 0},
    )
    assert forbidden.status_code == 403

    first = client.post(
        f"{API}/slots/{SLOT}/publish",
        headers=headers["admin"],
        json={"variant_id": baseline["id"], "expected_revision": 0, "note": "first"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["active_variant_id"] == baseline["id"]
    assert first.json()["revision"] == 1

    stale = client.post(
        f"{API}/slots/{SLOT}/publish",
        headers=headers["admin"],
        json={"variant_id": candidate["id"], "expected_revision": 0},
    )
    assert stale.status_code == 409

    second = client.post(
        f"{API}/slots/{SLOT}/publish",
        headers=headers["admin"],
        json={"variant_id": candidate["id"], "expected_revision": 1, "note": "second"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["active_variant_id"] == candidate["id"]
    assert second.json()["previous_variant_id"] == baseline["id"]
    assert second.json()["revision"] == 2

    rolled = client.post(
        f"{API}/slots/{SLOT}/rollback",
        headers=headers["admin"],
        json={"expected_revision": 2},
    )
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["active_variant_id"] == baseline["id"]
    assert rolled.json()["previous_variant_id"] == candidate["id"]
    assert rolled.json()["revision"] == 3

    conflict_rollback = client.post(
        f"{API}/slots/{SLOT}/rollback",
        headers=headers["admin"],
        json={"expected_revision": 2, "to_variant_id": candidate["id"]},
    )
    assert conflict_rollback.status_code == 409

    with Session(engine) as session:
        service = PromptVariantService(session)
        assert service.resolve_published_body(SLOT) == baseline["body"]
        pubs = session.exec(select(PromptPublication)).all()
        assert len(pubs) == 1
        assert pubs[0].revision == 3


def test_compare_does_not_auto_publish(ctx):
    client, engine, headers = ctx
    dataset_id, version_id = _dataset(client, headers)
    baseline = _variant(client, headers, "auto-base", "自动基线")
    candidate = _variant(client, headers, "auto-cand", "自动候选更长一些用于差异")
    compared = client.post(
        f"{API}/compares",
        headers=headers["admin"],
        json={
            "slot_key": SLOT,
            "baseline_variant_id": baseline["id"],
            "candidate_variant_id": candidate["id"],
            "dataset_id": dataset_id,
            "dataset_version_id": version_id,
            "provider": "fake",
        },
    )
    assert compared.status_code == 200
    assert compared.json()["auto_publish_blocked"] is True
    pub = client.get(f"{API}/slots/{SLOT}/publication", headers=headers["admin"])
    assert pub.status_code == 200
    assert pub.json() is None
    with Session(engine) as session:
        assert session.exec(select(PromptPublication)).first() is None
