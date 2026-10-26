"""Real evaluation targets come from tenant resources, never display fixtures."""

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.routes import evaluation as evaluation_routes
from app.core.config import settings
from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.models.evaluation import EvaluationDataset, EvaluationDatasetVersion
from app.models.rag import Document, DocumentChunk, EmbeddingIndex
from app.models.user import Tenant, User
from app.models.workflow import Workflow


def test_lists_only_real_tenant_resources_and_active_execution_users(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="target-a", name="A"))
        session.add(Tenant(id="target-b", name="B"))
        session.add(
            User(id="platform", tenant_id="target-a", username="platform", hashed_password="", role="system_admin")
        )
        session.add(User(id="member-a", tenant_id="target-a", username="member-a", hashed_password="", role="member"))
        session.add(
            User(
                id="disabled-a",
                tenant_id="target-a",
                username="disabled-a",
                hashed_password="",
                role="member",
                is_active=False,
            )
        )
        session.add(User(id="member-b", tenant_id="target-b", username="member-b", hashed_password="", role="member"))
        session.add(
            EmbeddingIndex(
                id="index-active",
                name="active",
                backend=settings.RAG_VECTOR_STORE,
                provider="p",
                model="m",
                dim=4,
                identity_key="identity",
                status="active",
            )
        )
        session.add(
            Document(
                id="doc-a",
                tenant_id="target-a",
                user_id="member-a",
                title="真实文档",
                content_hash="hash-a",
                version_group_id="vg-a",
                version_number=2,
                is_current=True,
                version_state="published",
                chunk_count=1,
            )
        )
        session.add(
            Document(
                id="doc-draft",
                tenant_id="target-a",
                user_id="member-a",
                title="草稿",
                version_group_id="vg-d",
                version_state="draft",
                chunk_count=1,
            )
        )
        session.add(
            Document(
                id="doc-b",
                tenant_id="target-b",
                user_id="member-b",
                title="其他租户",
                version_group_id="vg-b",
                chunk_count=1,
            )
        )
        session.add(
            DocumentChunk(
                id="chunk-a", tenant_id="target-a", document_id="doc-a",
                content="真实文档中可用于检索评测的业务事实内容。", index_id="index-active"
            )
        )
        session.add(
            DocumentChunk(
                id="chunk-d", tenant_id="target-a", document_id="doc-draft", content="草稿内容", index_id="index-active"
            )
        )
        session.add(
            DocumentChunk(
                id="chunk-b", tenant_id="target-b", document_id="doc-b", content="其他内容", index_id="index-active"
            )
        )
        session.add(
            Workflow(
                id="workflow-a",
                tenant_id="target-a",
                owner_id="member-a",
                name="真实流程",
                cron_expr="0 8 * * *",
                prompt_template="处理 {input}",
            )
        )
        session.add(
            EvaluationDataset(
                id="agent-cases",
                tenant_id="target-a",
                name="Agent cases",
                slug="agent-cases",
                family="agent",
                created_by="platform",
            )
        )
        session.add(
            EvaluationDatasetVersion(
                id="agent-cases-v1",
                dataset_id="agent-cases",
                tenant_id="target-a",
                version="1.0.0",
                status="approved",
                source="upload",
                content_hash="agent-case-hash",
                cases_json='[{"id":"agent-case-1","query":"test"}]',
                case_count=1,
                is_frozen=True,
                created_by="platform",
            )
        )
        session.commit()

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = database
    client = TestClient(app)
    audit_events = []

    async def record_audit(request, action, user=None, resource_type=None, resource_id=None, details=None):
        audit_events.append((action, user.id, resource_type, resource_id, details))

    monkeypatch.setattr(evaluation_routes.dispatcher, "submit", lambda bind, job_id: None)
    monkeypatch.setattr(evaluation_routes, "audit_event", record_audit)
    headers = {"Authorization": f"Bearer {create_access_token('platform', 'target-a', 'system_admin')}"}
    try:
        rag = client.get("/api/v1/evaluations/targets?tenant_id=target-a&target_type=rag", headers=headers)
        assert rag.status_code == 200
        assert [item["id"] for item in rag.json()["items"]] == ["doc-a"]
        assert rag.json()["items"][0]["metadata"]["index_id"] == "index-active"

        agent = client.get("/api/v1/evaluations/targets?tenant_id=target-a&target_type=agent", headers=headers)
        assert agent.status_code == 200
        assert [(item["id"], item["name"]) for item in agent.json()["items"]] == [("system-default", "系统默认 Agent")]

        workflow = client.get("/api/v1/evaluations/targets?tenant_id=target-a&target_type=workflow", headers=headers)
        assert workflow.status_code == 200
        assert [item["id"] for item in workflow.json()["items"]] == ["workflow-a"]

        users = client.get("/api/v1/evaluations/execution-users?tenant_id=target-a", headers=headers)
        assert users.status_code == 200
        assert [item["id"] for item in users.json()["items"]] == ["member-a"]

        created = client.post(
            "/api/v1/evaluations/jobs",
            headers=headers,
            json={
                "tenant_id": "target-a",
                "target_type": "rag",
                "target_ids": ["doc-a"],
                "idempotency_key": "real-target-api",
                "max_external_calls": 0,
            },
        )
        assert created.status_code == 202, created.text
        assert created.json()["job_kind"] == "real_resource"
        assert created.json()["target_type"] == "rag"
        assert created.json()["adapter_name"] == "builtin.rag"
        assert created.json()["case_total"] == 1
        assert audit_events[0][0].value == "evaluation_job_create"
        assert audit_events[0][1] == "platform"
        assert audit_events[0][4]["target_tenant_id"] == "target-a"
        assert audit_events[0][4]["execution_user_id"] is None

        agent_created = client.post(
            "/api/v1/evaluations/jobs",
            headers=headers,
            json={
                "tenant_id": "target-a",
                "dataset_id": "agent-cases",
                "dataset_version_id": "agent-cases-v1",
                "target_type": "agent",
                "target_ids": ["system-default"],
                "execution_user_id": "member-a",
                "idempotency_key": "real-agent-api",
                "max_external_calls": 1,
            },
        )
        assert agent_created.status_code == 202, agent_created.text
        assert agent_created.json()["execution_user_id"] == "member-a"
        assert audit_events[1][1] == "platform"
        assert audit_events[1][4]["initiator_user_id"] == "platform"
        assert audit_events[1][4]["execution_user_id"] == "member-a"
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()


def test_target_catalog_rejects_inactive_tenant_and_non_admin():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="inactive", name="Inactive", is_active=False))
        session.add(Tenant(id="active", name="Active"))
        session.add(
            User(id="platform-2", tenant_id="active", username="platform-2", hashed_password="", role="system_admin")
        )
        session.add(
            User(
                id="tenant-admin", tenant_id="active", username="tenant-admin", hashed_password="", role="tenant_admin"
            )
        )
        session.commit()

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = database
    client = TestClient(app)
    admin = {"Authorization": f"Bearer {create_access_token('platform-2', 'active', 'system_admin')}"}
    tenant = {"Authorization": f"Bearer {create_access_token('tenant-admin', 'active', 'tenant_admin')}"}
    try:
        assert (
            client.get("/api/v1/evaluations/targets?tenant_id=inactive&target_type=rag", headers=admin).status_code
            == 404
        )
        assert (
            client.get("/api/v1/evaluations/targets?tenant_id=active&target_type=rag", headers=tenant).status_code
            == 403
        )
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()
