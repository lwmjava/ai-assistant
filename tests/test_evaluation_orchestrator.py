"""Real-resource orchestration persists per-case evidence and a unified report."""

import asyncio
import json
import threading
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.evaluation import EvaluationCaseResult, EvaluationDatasetVersion, EvaluationJob
from app.models.user import Tenant, User
from app.models.workflow import Workflow
from app.rag.vectorstore.base import ChunkResult
from app.services import evaluation_orchestrator
from app.services.evaluation_orchestrator import run_real_evaluation
from app.services.evaluation_reports import ReportEntry, ReportService


@pytest.fixture(autouse=True)
def isolated_runner_preflight(monkeypatch):
    # These runner-unit fixtures deliberately omit catalog resources and initiators.
    # Fresh database preflight and drift rejection are covered by boundary tests.
    monkeypatch.setattr(evaluation_orchestrator, "_validate_job", lambda *args: None)


def test_real_rag_orchestrator_uses_runtime_search_and_persists_evidence(
    monkeypatch, tmp_path: Path
):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    cases = [
        {
            "case_id": "hit",
            "split": "validation",
            "tier": "silver",
            "query": "命中查询",
            "expected_document_ids": ["doc-a"],
        },
        {
            "case_id": "miss",
            "split": "validation",
            "tier": "silver",
            "query": "未命中查询",
            "expected_document_ids": ["doc-b"],
        },
    ]
    with Session(engine) as session:
        session.add(Tenant(id="orchestrator-tenant", name="T"))
        session.add(
            User(
                id="orchestrator-admin",
                tenant_id="orchestrator-tenant",
                username="admin",
                hashed_password="",
                role="system_admin",
            )
        )
        session.add(
            EvaluationDatasetVersion(
                id="cases-v1",
                dataset_id="cases",
                tenant_id="orchestrator-tenant",
                version="1",
                status="approved",
                source="ai_generated",
                content_hash="snapshot-hash",
                cases_json=json.dumps(cases),
                case_count=2,
                created_by="orchestrator-admin",
            )
        )
        job = EvaluationJob(
            id="real-job",
            tenant_id="orchestrator-tenant",
            submitted_by="orchestrator-admin",
            dataset_id="cases",
            dataset_version_id="cases-v1",
            job_kind="real_resource",
            target_type="rag",
            target_ids_json='["doc-a"]',
            target_snapshot_json='{"targets":[]}',
            adapter_name="builtin.rag",
            adapter_version="1.0.0",
            metric_schema_version="evaluation-metrics-v1",
            options_json="{}",
            budget_json='{"max_external_calls":0}',
            runner="retrieval",
            mode="real",
            idempotency_key="real-job",
            request_hash="request-hash",
            input_hash="snapshot-hash",
            max_external_calls=0,
            case_total=2,
        )
        session.add(job)
        session.commit()

        class FakeRAG:
            def __init__(self, bound_session, tenant_id):
                assert bound_session is session
                assert tenant_id == "orchestrator-tenant"

            async def search(self, query: str, top_k: int):
                assert top_k == 10
                document_id = "doc-a" if query == "命中查询" else "doc-c"
                return [
                    ChunkResult(
                        id=f"chunk-{document_id}",
                        content="evidence",
                        source="source",
                        document_id=document_id,
                        score=1.0,
                    )
                ]

        monkeypatch.setattr(evaluation_orchestrator, "RAGService", FakeRAG)
        report_path = tmp_path / "real.json"
        result = asyncio.run(
            run_real_evaluation(session, job, threading.Event(), report_path)
        )
        assert result == report_path
        stored = list(session.exec(select(EvaluationCaseResult)).all())
        assert [(item.case_id, item.status) for item in stored] == [
            ("hit", "passed"),
            ("miss", "failed"),
        ]
        assert session.get(EvaluationJob, "real-job").case_completed == 2

        raw = json.loads(report_path.read_text(encoding="utf-8"))
        assert raw["metrics"]["recall_at_5"] == 0.5
        assert raw["status"] == "failed"
        service = ReportService(
            tmp_path,
            (
                ReportEntry(
                    id="real-report",
                    family="retrieval",
                    path="real.json",
                    scope="tenant",
                    tenant_id="orchestrator-tenant",
                ),
            ),
        )
        detail = service.detail(session.get(User, "orchestrator-admin"), "real-report")
        assert detail.protocol == "real-evaluation-report-1.0.0"
        assert detail.status == "failed"
        assert detail.failure_count == 1
    engine.dispose()


@pytest.mark.parametrize("with_expectations", [True, False])
def test_real_agent_uses_selected_tenant_principal_and_budget(monkeypatch, tmp_path: Path, with_expectations):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    cases = [
        {
            "case_id": "agent-case",
            "query": "请处理这个真实请求",
            "expected_route": "full",
            "expected_tools": ["calculator"],
        }
    ]
    if not with_expectations:
        cases[0].pop("expected_route")
        cases[0].pop("expected_tools")
    with Session(engine) as session:
        session.add(Tenant(id="agent-tenant", name="Agent tenant"))
        session.add(
            User(
                id="agent-user",
                tenant_id="agent-tenant",
                username="agent-user",
                hashed_password="",
                role="member",
            )
        )
        session.add(
            EvaluationDatasetVersion(
                id="agent-cases-v1",
                dataset_id="agent-cases",
                tenant_id="agent-tenant",
                version="1",
                status="approved",
                source="upload",
                content_hash="agent-hash",
                cases_json=json.dumps(cases),
                case_count=1,
                created_by="admin",
            )
        )
        job = EvaluationJob(
            id="agent-job",
            tenant_id="agent-tenant",
            submitted_by="platform-admin",
            dataset_id="agent-cases",
            dataset_version_id="agent-cases-v1",
            job_kind="real_resource",
            target_type="agent",
            target_ids_json='["system-default"]',
            target_snapshot_json='{"targets":[]}',
            adapter_name="builtin.agent",
            adapter_version="1.0.0",
            metric_schema_version="evaluation-metrics-v1",
            execution_user_id="agent-user",
            execution_principal_json=(
                '{"id":"agent-user","username":"agent-user",'
                '"role":"member","token_version":0}'
            ),
            options_json="{}",
            budget_json='{"max_external_calls":5}',
            runner="agent",
            mode="real",
            idempotency_key="agent-job",
            request_hash="request",
            input_hash="agent-hash",
            max_external_calls=5,
            case_total=1,
        )
        session.add(job)
        session.commit()

        async def fake_agent(bound_session, user, query, budget):
            assert bound_session is session
            assert user.id == "agent-user"
            assert query == "请处理这个真实请求"
            budget.consume()
            budget.consume()
            return evaluation_orchestrator._AgentOutcome(
                answer="完成",
                route="full",
                tools=["calculator"],
                error_code=None,
                elapsed_ms=12.5,
            )

        monkeypatch.setattr(evaluation_orchestrator, "_run_isolated_agent", fake_agent)
        report = tmp_path / "agent.json"
        asyncio.run(run_real_evaluation(session, job, threading.Event(), report))
        refreshed = session.get(EvaluationJob, job.id)
        assert refreshed.case_completed == 1
        assert refreshed.external_calls_used == 2
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["metrics"]["route_accuracy"] == (1.0 if with_expectations else None)
        assert payload["metrics"]["followup_recall"] is None
        assert payload["metrics"]["tool_call_validity_recall"] is None
        if not with_expectations:
            assert payload["metrics"]["tool_selection_recall"] is None
        assert payload["status"] == "passed"
    engine.dispose()


def test_real_agent_rejects_revoked_execution_principal(tmp_path: Path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="revoked-tenant", name="Revoked tenant"))
        session.add(
            User(
                id="revoked-user",
                tenant_id="revoked-tenant",
                username="revoked-user",
                hashed_password="",
                role="member",
                token_version=2,
            )
        )
        session.add(
            EvaluationDatasetVersion(
                id="revoked-cases-v1",
                dataset_id="revoked-cases",
                tenant_id="revoked-tenant",
                version="1",
                status="approved",
                source="upload",
                content_hash="revoked-hash",
                cases_json='[{"case_id":"case","query":"test"}]',
                case_count=1,
                created_by="admin",
            )
        )
        job = EvaluationJob(
            id="revoked-job",
            tenant_id="revoked-tenant",
            submitted_by="platform-admin",
            dataset_id="revoked-cases",
            dataset_version_id="revoked-cases-v1",
            job_kind="real_resource",
            target_type="agent",
            target_ids_json='["system-default"]',
            target_snapshot_json='{"targets":[]}',
            adapter_name="builtin.agent",
            adapter_version="1.0.0",
            metric_schema_version="evaluation-metrics-v1",
            execution_user_id="revoked-user",
            execution_principal_json=(
                '{"id":"revoked-user","username":"revoked-user",'
                '"role":"member","token_version":1}'
            ),
            options_json="{}",
            budget_json='{"max_external_calls":1}',
            runner="agent",
            mode="real",
            idempotency_key="revoked-job",
            request_hash="request",
            input_hash="revoked-hash",
            max_external_calls=1,
            case_total=1,
        )
        session.add(job)
        session.commit()

        with pytest.raises(
            evaluation_orchestrator.EvaluationOrchestratorError,
            match="execution_principal_changed",
        ):
            asyncio.run(run_real_evaluation(session, job, threading.Event(), tmp_path / "revoked.json"))
    engine.dispose()


def test_real_workflow_runs_engine_in_isolated_database_without_webhook(monkeypatch, tmp_path: Path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    cases = [
        {
            "case_id": "workflow-case",
            "query": "订单 42",
            "expected_answer_points": ["完成"],
            "forbidden_answer_points": ["失败"],
        }
    ]
    with Session(engine) as session:
        session.add(Tenant(id="workflow-tenant", name="Workflow tenant"))
        session.add(
            User(
                id="workflow-owner",
                tenant_id="workflow-tenant",
                username="workflow-owner",
                hashed_password="",
                role="member",
            )
        )
        session.add(
            Workflow(
                id="workflow-target",
                tenant_id="workflow-tenant",
                owner_id="workflow-owner",
                name="真实工作流",
                cron_expr="0 8 * * *",
                prompt_template="处理 {input}",
                webhook_url="https://must-not-run.invalid/hook",
            )
        )
        session.add(
            EvaluationDatasetVersion(
                id="workflow-cases-v1",
                dataset_id="workflow-cases",
                tenant_id="workflow-tenant",
                version="1",
                status="approved",
                source="upload",
                content_hash="workflow-hash",
                cases_json=json.dumps(cases),
                case_count=1,
                created_by="admin",
            )
        )
        job = EvaluationJob(
            id="workflow-job",
            tenant_id="workflow-tenant",
            submitted_by="platform-admin",
            dataset_id="workflow-cases",
            dataset_version_id="workflow-cases-v1",
            job_kind="real_resource",
            target_type="workflow",
            target_ids_json='["workflow-target"]',
            target_snapshot_json='{"targets":[]}',
            adapter_name="builtin.workflow",
            adapter_version="1.0.0",
            metric_schema_version="evaluation-metrics-v1",
            options_json="{}",
            budget_json='{"max_external_calls":5}',
            runner="workflow",
            mode="real",
            idempotency_key="workflow-job",
            request_hash="request",
            input_hash="workflow-hash",
            max_external_calls=5,
            case_total=1,
        )
        session.add(job)
        session.commit()

        async def fake_agent(bound_session, user, query, budget):
            assert bound_session is session
            assert user.id == "workflow-owner"
            assert query == "处理 订单 42"
            budget.consume()
            return evaluation_orchestrator._AgentOutcome(
                answer="处理完成",
                route="full",
                tools=[],
                error_code=None,
                elapsed_ms=9.0,
            )

        monkeypatch.setattr(evaluation_orchestrator, "_run_isolated_agent", fake_agent)
        report = tmp_path / "workflow.json"
        asyncio.run(run_real_evaluation(session, job, threading.Event(), report))
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["metrics"]["completion_rate"] == 1.0
        assert payload["cases"][0]["evidence"] == {
            "isolated": True,
            "webhook_suppressed": True,
            "execution_status": "success",
            "answer_present": True,
        }
        assert session.exec(select(EvaluationCaseResult)).one().status == "passed"
    engine.dispose()
