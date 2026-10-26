"""Built-in evaluation adapters freeze real tenant resources and principals."""

import threading

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.models.evaluation import EvaluationDataset, EvaluationDatasetVersion
from app.models.rag import Document, DocumentChunk, EmbeddingIndex
from app.models.user import Tenant, User
from app.models.workflow import Workflow
from app.services.evaluation_adapters import (
    EvaluationAdapterError,
    builtin_evaluation_adapters,
)
from app.services.evaluation_jobs import EvaluationJobService, EvaluationJobValidationError
from app.services.evaluation_orchestrator import EvaluationOrchestratorError, _validate_job


@pytest.fixture
def adapter_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="adapter-a", name="A"))
        session.add(Tenant(id="adapter-b", name="B"))
        session.add(
            User(
                id="platform",
                tenant_id="adapter-a",
                username="platform",
                hashed_password="",
                role="system_admin",
            )
        )
        session.add(
            User(
                id="run-user",
                tenant_id="adapter-a",
                username="run-user",
                hashed_password="",
                role="member",
            )
        )
        session.add(
            User(
                id="other-user",
                tenant_id="adapter-b",
                username="other-user",
                hashed_password="",
                role="member",
            )
        )
        session.add(
            EmbeddingIndex(
                id="adapter-index",
                name="active",
                backend=settings.RAG_VECTOR_STORE,
                provider="provider",
                model="model",
                dim=8,
                identity_key="adapter-identity",
                status="active",
            )
        )
        session.add(
            Document(
                id="adapter-doc",
                tenant_id="adapter-a",
                user_id="run-user",
                title="真实文档",
                content_hash="doc-hash",
                version_group_id="group",
                version_number=3,
                is_current=True,
                version_state="published",
                chunk_count=1,
            )
        )
        session.add(
            DocumentChunk(
                id="adapter-chunk",
                tenant_id="adapter-a",
                document_id="adapter-doc",
                content="真实文档包含可用于检索评测的业务知识和证据内容。",
                index_id="adapter-index",
            )
        )
        session.add(
            Workflow(
                id="adapter-workflow",
                tenant_id="adapter-a",
                owner_id="run-user",
                name="真实流程",
                cron_expr="0 8 * * *",
                prompt_template="处理输入",
            )
        )
        session.add(
            EvaluationDataset(
                id="rag-cases",
                tenant_id="adapter-a",
                name="RAG cases",
                slug="rag-cases",
                family="retrieval",
                created_by="platform",
            )
        )
        session.add(
            EvaluationDatasetVersion(
                id="rag-cases-v1",
                dataset_id="rag-cases",
                tenant_id="adapter-a",
                version="1.0.0",
                status="approved",
                source="ai_generated",
                content_hash="case-hash",
                cases_json='[{"id":"case-1"}]',
                case_count=1,
                created_by="platform",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def test_registry_exposes_only_three_built_in_adapters():
    assert [item["target_type"] for item in builtin_evaluation_adapters.descriptors()] == [
        "rag",
        "agent",
        "workflow",
    ]
    with pytest.raises(EvaluationAdapterError, match="unsupported_target_type"):
        builtin_evaluation_adapters.get("plugin")


@pytest.mark.parametrize("change", ["document", "index", "initiator", "tenant", "cancel"])
def test_real_job_revalidates_frozen_resources_and_authority(adapter_session, change):
    platform = adapter_session.get(User, "platform")
    job, _ = EvaluationJobService(adapter_session).create_real(
        platform, tenant_id="adapter-a", dataset_id="rag-cases",
        dataset_version_id="rag-cases-v1", target_type="rag", target_ids=["adapter-doc"],
        execution_user_id=None, enable_generation=False, enable_judge=False,
        idempotency_key="drift-" + change, max_external_calls=0,
    )
    _validate_job(adapter_session, job, threading.Event())
    if change == "document":
        row = adapter_session.get(Document, "adapter-doc")
        row.content_hash = "changed"
    elif change == "index":
        row = adapter_session.get(EmbeddingIndex, "adapter-index")
        row.status = "inactive"
    elif change == "initiator":
        row = platform
        row.token_version += 1
    elif change == "tenant":
        row = adapter_session.get(Tenant, "adapter-a")
        row.is_active = False
    else:
        row = job
        row.status = "cancel_requested"
    adapter_session.add(row)
    adapter_session.commit()
    if change == "cancel":
        import asyncio

        with pytest.raises(asyncio.CancelledError):
            _validate_job(adapter_session, job, threading.Event())
    else:
        with pytest.raises(EvaluationOrchestratorError):
            _validate_job(adapter_session, job, threading.Event())


def test_rag_snapshot_freezes_document_and_index_identity(adapter_session: Session):
    snapshot = builtin_evaluation_adapters.get("rag").snapshot(
        adapter_session,
        tenant_id="adapter-a",
        target_ids=["adapter-doc"],
        execution_user_id=None,
    )
    assert snapshot.adapter_name == "builtin.rag"
    assert snapshot.targets[0]["metadata"] == {
        "version_group_id": "group",
        "version_number": 3,
        "content_hash": "doc-hash",
        "chunk_count": 1,
        "index_id": "adapter-index",
        "embedding_provider": "provider",
        "embedding_model": "model",
        "embedding_dim": 8,
        "vector_backend": settings.RAG_VECTOR_STORE,
    }


def test_agent_snapshot_requires_active_user_from_target_tenant(adapter_session: Session):
    adapter = builtin_evaluation_adapters.get("agent")
    snapshot = adapter.snapshot(
        adapter_session,
        tenant_id="adapter-a",
        target_ids=["system-default"],
        execution_user_id="run-user",
    )
    assert snapshot.execution_principal == {
        "id": "run-user",
        "username": "run-user",
        "role": "member",
        "token_version": 0,
    }
    with pytest.raises(EvaluationAdapterError, match="execution_user_unavailable"):
        adapter.snapshot(
            adapter_session,
            tenant_id="adapter-a",
            target_ids=["system-default"],
            execution_user_id="other-user",
        )


def test_workflow_snapshot_rejects_missing_or_unrunnable_target(adapter_session: Session):
    adapter = builtin_evaluation_adapters.get("workflow")
    snapshot = adapter.snapshot(
        adapter_session,
        tenant_id="adapter-a",
        target_ids=["adapter-workflow"],
        execution_user_id=None,
    )
    assert snapshot.targets[0]["metadata"]["owner_id"] == "run-user"
    with pytest.raises(EvaluationAdapterError, match="target_unavailable"):
        adapter.snapshot(
            adapter_session,
            tenant_id="adapter-a",
            target_ids=["missing"],
            execution_user_id=None,
        )


def test_real_job_freezes_adapter_target_budget_and_initiator(adapter_session: Session):
    platform = adapter_session.get(User, "platform")
    service = EvaluationJobService(adapter_session)
    row, created = service.create_real(
        platform,
        tenant_id="adapter-a",
        dataset_id="rag-cases",
        dataset_version_id="rag-cases-v1",
        target_type="rag",
        target_ids=["adapter-doc"],
        execution_user_id=None,
        enable_generation=False,
        enable_judge=False,
        idempotency_key="real-rag-1",
        max_external_calls=0,
    )
    assert created is True
    assert row.job_kind == "real_resource"
    assert row.submitted_by == "platform"
    assert row.tenant_id == "adapter-a"
    assert row.adapter_name == "builtin.rag"
    assert row.target_type == "rag"
    assert row.case_total == 1
    assert '"id":"adapter-doc"' in row.target_snapshot_json

    duplicate, duplicate_created = service.create_real(
        platform,
        tenant_id="adapter-a",
        dataset_id="rag-cases",
        dataset_version_id="rag-cases-v1",
        target_type="rag",
        target_ids=["adapter-doc"],
        execution_user_id=None,
        enable_generation=False,
        enable_judge=False,
        idempotency_key="real-rag-1",
        max_external_calls=0,
    )
    assert duplicate_created is False
    assert duplicate.id == row.id


def test_real_job_requires_explicit_budget_for_paid_stages(adapter_session: Session):
    platform = adapter_session.get(User, "platform")
    with pytest.raises(EvaluationJobValidationError, match="external_budget_required"):
        EvaluationJobService(adapter_session).create_real(
            platform,
            tenant_id="adapter-a",
            dataset_id="rag-cases",
            dataset_version_id="rag-cases-v1",
            target_type="rag",
            target_ids=["adapter-doc"],
            execution_user_id=None,
            enable_generation=True,
            enable_judge=False,
            idempotency_key="paid-without-budget",
            max_external_calls=0,
        )


def test_paid_quality_requires_explicit_data_authorization(adapter_session: Session):
    platform = adapter_session.get(User, "platform")
    with pytest.raises(EvaluationJobValidationError, match="quality_data_confirmation_required"):
        EvaluationJobService(adapter_session).create_real(
            platform,
            tenant_id="adapter-a",
            dataset_id="rag-cases",
            dataset_version_id="rag-cases-v1",
            target_type="rag",
            target_ids=["adapter-doc"],
            execution_user_id=None,
            enable_generation=True,
            enable_judge=False,
            idempotency_key="paid-stage-unavailable",
            max_external_calls=1,
        )


def test_real_rag_job_can_generate_grounded_silver_without_upload(adapter_session: Session):
    platform = adapter_session.get(User, "platform")
    row, created = EvaluationJobService(adapter_session).create_real(
        platform,
        tenant_id="adapter-a",
        dataset_id=None,
        dataset_version_id=None,
        target_type="rag",
        target_ids=["adapter-doc"],
        execution_user_id=None,
        enable_generation=False,
        enable_judge=False,
        idempotency_key="auto-silver",
        max_external_calls=0,
    )
    assert created is True
    version = adapter_session.get(EvaluationDatasetVersion, row.dataset_version_id)
    assert version is not None
    assert version.status == "approved"
    assert version.source == "ai_generated"
    assert version.schema_name == "grounded-rag-silver-v1"
    assert version.review_decision == "AUTO_SILVER"
    assert '"expected_document_ids": ["adapter-doc"]' in version.cases_json
