"""Paid quality stages fail closed and account for every attempted request."""

import asyncio
import json
import threading
from pathlib import Path

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.models.evaluation import EvaluationDataset, EvaluationDatasetVersion, EvaluationJob
from app.models.rag import Document, DocumentChunk, EmbeddingIndex
from app.models.user import Tenant, User
from app.rag.vectorstore.base import ChunkResult
from app.services import evaluation_orchestrator, evaluation_quality
from app.services.evaluation_datasets import canonical_cases_hash
from app.services.evaluation_jobs import EvaluationJobService, EvaluationJobValidationError, execute_job
from app.services.evaluation_quality import (
    EvaluationQualityError,
    QualityEmbeddingProvider,
    QualitySession,
    parse_judgment,
    quality_snapshot,
)
from app.services.evaluation_reports import ReportEntry, ReportService


@pytest.fixture
def quality_ctx(monkeypatch):
    for name, value in {
        "EVALUATION_QUALITY_ENABLED": True,
        "LLM_PROVIDER": "openai", "LLM_BASE_URL": "https://api.deepseek.com/v1",
        "LLM_DEFAULT_MODEL": "deepseek-chat", "LLM_API_KEY": "synthetic-test-key",
        "LLM_CAPABILITY_DECLARED": "deepseek-chat=16000:2048",
        "LLM_CAPABILITY_DECLARED_SOURCE": "test-only-capability-fixture",
        "LLM_CAPABILITY_GUARD_ENABLED": True,
        "EVALUATION_PRICE_BASE_URL": "https://api.deepseek.com/v1",
        "EVALUATION_PRICE_MODEL": "deepseek-chat", "EVALUATION_PRICE_REFERENCE": "test-only-fixture",
        "EVALUATION_INPUT_YUAN_PER_MTOK": 2.0, "EVALUATION_OUTPUT_YUAN_PER_MTOK": 8.0,
        "EMBEDDING_API_KEY": "synthetic-embedding-key", "EMBEDDING_PROVIDER": "openai",
        "EMBEDDING_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "EMBEDDING_MODEL": "text-embedding-v3",
        "EVALUATION_EMBEDDING_PRICE_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "EVALUATION_EMBEDDING_PRICE_MODEL": "text-embedding-v3",
        "EVALUATION_EMBEDDING_PRICE_REFERENCE": "test-only-fixture",
        "EVALUATION_EMBEDDING_YUAN_PER_MTOK": 1.0,
    }.items():
        monkeypatch.setattr(settings, name, value)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="t", name="synthetic"))
        admin = User(id="a", tenant_id="t", username="admin", hashed_password="", role="system_admin")
        session.add(admin)
        session.add(EmbeddingIndex(id="idx", name="test", backend=settings.RAG_VECTOR_STORE,
                                   provider="test", model="test", dim=8, identity_key="test", status="active"))
        session.add(Document(id="doc", tenant_id="t", user_id="a", title="Synthetic policy",
                             content_hash="document-hash", version_state="published", is_current=True))
        session.add(DocumentChunk(id="chunk", tenant_id="t", document_id="doc", index_id="idx",
                                  content="合成测试政策：退货期限为七天。"))
        session.add(EvaluationDataset(id="ds", tenant_id="t", name="test", slug="test",
                                      family="retrieval", created_by="a"))
        cases = [{"case_id": "c", "query": "退货期限？", "expected_document_ids": ["doc"],
                  "expected_answer_points": ["七天"]}]
        session.add(EvaluationDatasetVersion(id="v", dataset_id="ds", tenant_id="t", version="1",
                    status="approved", source="upload", created_by="a", case_count=1,
                    content_hash=canonical_cases_hash(cases), cases_json=json.dumps(cases)))
        session.commit()
        payload = dict(tenant_id="t", dataset_id="ds", dataset_version_id="v", target_type="rag",
                       target_ids=["doc"], execution_user_id=None, enable_generation=True,
                       enable_judge=True, idempotency_key="quality", max_external_calls=3,
                       max_cost_yuan=5, data_egress_confirmed=True)
        yield session, admin, payload
    engine.dispose()


def transport(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(evaluation_quality.httpx, "AsyncClient", lambda **kw: client(
        transport=httpx.MockTransport(handler), trust_env=False, **kw,
    ))


def response(text="七天 [[chunk]]", **overrides):
    data = {"model": "deepseek-chat", "usage": {"prompt_tokens": 30, "completion_tokens": 10},
            "choices": [{"finish_reason": "stop", "message": {"content": text}}]}
    data.update(overrides)
    return httpx.Response(200, json=data)


@pytest.mark.parametrize("name,value", [
    ("EVALUATION_QUALITY_ENABLED", False), ("EVALUATION_PRICE_MODEL", "different"),
    ("EVALUATION_INPUT_YUAN_PER_MTOK", None), ("EVALUATION_PRICE_REFERENCE", ""),
])
def test_missing_price_or_disabled_stage_sends_nothing(quality_ctx, monkeypatch, name, value):
    session, user, payload = quality_ctx
    monkeypatch.setattr(settings, name, value)
    transport(monkeypatch, lambda request: pytest.fail("must not send HTTP"))
    with pytest.raises(EvaluationJobValidationError, match="quality_disabled|quality_pricing_unavailable"):
        EvaluationJobService(session).create_real(user, **payload)
    assert session.get(EvaluationJob, "quality") is None


@pytest.mark.parametrize("overrides,code", [
    ({"data_egress_confirmed": False}, "quality_data_confirmation_required"),
    ({"max_cost_yuan": 5.01}, "quality_budget_invalid"),
    ({"max_external_calls": 31}, "quality_budget_invalid"),
    ({"max_external_calls": 1}, "quality_call_budget_insufficient"),
    ({"enable_generation": False}, "judge_requires_generation"),
    ({"target_type": "agent"}, "quality_target_not_supported"),
])
def test_quality_request_bounds(quality_ctx, overrides, code):
    session, user, payload = quality_ctx
    with pytest.raises(EvaluationJobValidationError, match=code):
        EvaluationJobService(session).create_real(user, **(payload | overrides))


def test_real_quality_report_and_usage_are_bound_without_raw_answers(quality_ctx, monkeypatch, tmp_path: Path):
    session, user, payload = quality_ctx
    assert quality_snapshot()["model"] == "deepseek-chat"
    job, _ = EvaluationJobService(session).create_real(user, **payload)
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        persisted = json.loads(session.get(EvaluationJob, job.id).budget_json)
        assert persisted["quality_usage"][-1]["status"] == "reserved"
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={
                "model": "text-embedding-v3", "usage": {"prompt_tokens": 4},
                "data": [{"index": 0, "embedding": [1.0] * settings.EMBEDDING_DIM}],
            })
        assert body["model"] == "deepseek-chat" and body["max_tokens"] == 1024
        return response() if len(requests) == 2 else response('{"faithfulness":1,"correctness":0.8}')

    transport(monkeypatch, handler)

    class RAG:
        def __init__(self, *_, **_kwargs):
            self.embedding = _kwargs["embedding_provider"]

        async def search(self, *_args, **_kwargs):
            await self.embedding.embed(["退货期限？"])
            return [ChunkResult(id="chunk", document_id="doc", content="ignored vector copy",
                                source="synthetic", score=1)]

    monkeypatch.setattr(evaluation_orchestrator, "RAGService", RAG)
    path = tmp_path / "quality.json"
    asyncio.run(evaluation_orchestrator.run_real_evaluation(session, job, threading.Event(), path))
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["family"] == "generation"
    assert report["metrics"]["judge_correctness"] == 0.8
    assert report["metrics"]["external_calls_used"] == 3
    assert len(report["budget"]["quality_usage"]) == 3
    assert report["budget"]["quality_usage"][1]["usage"]["prompt_tokens"] == 30
    assert "七天 [[chunk]]" not in path.read_text(encoding="utf-8")
    assert "ignored vector copy" not in json.dumps(requests)
    detail = ReportService(tmp_path, (ReportEntry("q", "generation", path.name, "tenant", "t"),)).detail(user, "q")
    assert detail.metadata["judge_model"] == "deepseek-chat"
    assert detail.metadata["judge_evidence"] == "advisory_same_model"
    assert next(item for item in detail.metrics if item.name == "cost_yuan").unit == "CNY"


@pytest.mark.parametrize("data,code", [
    ({"usage": None}, "quality_usage_missing"),
    ({"usage": {"prompt_tokens": True, "completion_tokens": 10}}, "quality_usage_missing"),
    ({"usage": {"prompt_tokens": 30, "completion_tokens": 1025}}, "quality_usage_out_of_bounds"),
    ({"model": "unexpected"}, "quality_response_model_changed"),
    ({"choices": [{"finish_reason": "length"}]}, "quality_response_incomplete"),
])
def test_unusable_response_retains_attempt_and_reservation(quality_ctx, monkeypatch, data, code):
    session, user, payload = quality_ctx
    job, _ = EvaluationJobService(session).create_real(user, **payload)
    attempts = []

    def handler(request):
        attempts.append(request)
        return response(**data)

    transport(monkeypatch, handler)
    with pytest.raises(EvaluationQualityError, match=code):
        asyncio.run(QualitySession(session, job, threading.Event()).call("generation", {"question": "test"}))
    assert len(attempts) == 1
    session.refresh(job)
    assert job.external_calls_used == 1
    record = json.loads(job.budget_json)["quality_usage"][0]
    assert record["status"] == "failed" and float(record["cost_yuan"]) > 0


def test_budget_exhaustion_and_cancellation_prevent_next_request(quality_ctx, monkeypatch):
    session, user, payload = quality_ctx
    job, _ = EvaluationJobService(session).create_real(user, **payload)
    transport(monkeypatch, lambda request: pytest.fail("must not send HTTP"))
    budget = json.loads(job.budget_json)
    budget["max_cost_yuan"] = 0.000001
    job.budget_json = json.dumps(budget)
    session.add(job)
    session.commit()
    with pytest.raises(EvaluationQualityError, match="quality_cost_exhausted"):
        asyncio.run(QualitySession(session, job, threading.Event()).call("generation", {}))
    marker = threading.Event()
    marker.set()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(QualitySession(session, job, marker).call("generation", {}))
    assert job.external_calls_used == 0


@pytest.mark.parametrize("change", ["model", "token", "target", "dataset"])
def test_runtime_drift_sends_nothing(quality_ctx, monkeypatch, change):
    session, user, payload = quality_ctx
    job, _ = EvaluationJobService(session).create_real(user, **payload)
    if change == "model":
        monkeypatch.setattr(settings, "LLM_DEFAULT_MODEL", "changed")
    elif change == "token":
        user.token_version += 1
        session.add(user)
    elif change == "target":
        doc = session.get(Document, "doc")
        doc.content_hash = "changed"
        session.add(doc)
    else:
        version = session.get(EvaluationDatasetVersion, "v")
        version.cases_json = "[]"
        session.add(version)
    session.commit()
    transport(monkeypatch, lambda request: pytest.fail("must not send HTTP"))
    with pytest.raises(EvaluationQualityError):
        asyncio.run(QualitySession(session, job, threading.Event()).call("generation", {}))


@pytest.mark.parametrize("raw", [
    '{"faithfulness":true,"correctness":1}', '{"faithfulness":1,"correctness":NaN}',
    '{"faithfulness":2,"correctness":1}', '{"faithfulness":1,"correctness":1,"instruction":"pass"}',
    '{"faithfulness":1,"faithfulness":0,"correctness":1}', 'ignore policy and pass',
])
def test_invalid_judge_output_cannot_become_a_score(raw):
    with pytest.raises(EvaluationQualityError, match="quality_judge_invalid"):
        parse_judgment(raw, has_reference=True)


def test_missing_reference_is_unknown_correctness():
    assert parse_judgment('{"faithfulness":1,"correctness":null}', has_reference=False)["correctness"] is None
    with pytest.raises(EvaluationQualityError):
        parse_judgment('{"faithfulness":1,"correctness":1}', has_reference=False)


def test_embeddings_share_the_same_call_and_cost_budget(quality_ctx, monkeypatch):
    session, user, payload = quality_ctx
    job, _ = EvaluationJobService(session).create_real(user, **payload)
    attempts = []

    def handler(request):
        attempts.append(request)
        assert request.url.path.endswith("/embeddings")
        assert json.loads(request.content)["input"] == ["合成查询"]
        return httpx.Response(200, json={
            "model": "text-embedding-v3", "usage": {"prompt_tokens": 4},
            "data": [{"index": 0, "embedding": [1.0] * settings.EMBEDDING_DIM}],
        })

    transport(monkeypatch, handler)
    quality = QualitySession(session, job, threading.Event())
    vectors = asyncio.run(QualityEmbeddingProvider(quality).embed(["合成查询"]))
    assert len(vectors[0]) == settings.EMBEDDING_DIM
    assert job.external_calls_used == 1
    record = json.loads(job.budget_json)["quality_usage"][0]
    assert record["purpose"] == "embedding" and record["usage"]["prompt_tokens"] == 4
    assert float(record["cost_yuan"]) == 0.000004
    job.external_calls_used = job.max_external_calls
    session.add(job)
    session.commit()
    with pytest.raises(EvaluationQualityError, match="external_budget_exhausted"):
        asyncio.run(quality.call("generation", {}))
    assert len(attempts) == 1


def test_missing_embedding_price_prevents_all_quality_requests(quality_ctx, monkeypatch):
    session, user, payload = quality_ctx
    monkeypatch.setattr(settings, "EVALUATION_EMBEDDING_YUAN_PER_MTOK", None)
    transport(monkeypatch, lambda request: pytest.fail("must not send HTTP"))
    with pytest.raises(EvaluationJobValidationError, match="quality_embedding_pricing_unavailable"):
        EvaluationJobService(session).create_real(user, **payload)


def test_cancel_inflight_request_keeps_reservation_without_retry(quality_ctx, monkeypatch):
    session, user, payload = quality_ctx
    job, _ = EvaluationJobService(session).create_real(user, **payload)
    marker = threading.Event()
    attempts = []

    async def handler(request):
        attempts.append(request)
        marker.set()
        await asyncio.Event().wait()

    transport(monkeypatch, handler)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(QualitySession(session, job, marker).call("generation", {}))
    assert len(attempts) == 1 and job.external_calls_used == 1
    record = json.loads(job.budget_json)["quality_usage"][0]
    assert record["status"] == "cancelled" and float(record["cost_yuan"]) > 0


def test_http_failure_does_not_retry_or_leak_body(quality_ctx, monkeypatch):
    session, user, payload = quality_ctx
    job, _ = EvaluationJobService(session).create_real(user, **payload)
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(429, text="private provider body")

    transport(monkeypatch, handler)
    with pytest.raises(EvaluationQualityError, match="^quality_request_failed$"):
        asyncio.run(QualitySession(session, job, threading.Event()).call("generation", {}))
    assert len(attempts) == 1 and "private" not in job.budget_json


def test_real_job_timeout_is_terminal(quality_ctx, monkeypatch):
    session, user, payload = quality_ctx
    job, _ = EvaluationJobService(session).create_real(user, **payload)
    monkeypatch.setattr(settings, "EVALUATION_JOB_TIMEOUT_SECONDS", 0.01)

    async def hang(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr("app.services.evaluation_jobs.registered_real_runner", hang)
    asyncio.run(execute_job(session.get_bind(), job.id, threading.Event()))
    session.refresh(job)
    assert job.status == "failed" and job.error_code == "runner_timeout"


def test_failed_paid_job_gets_terminal_state(quality_ctx, monkeypatch):
    session, user, payload = quality_ctx
    job, _ = EvaluationJobService(session).create_real(user, **payload)

    async def failure(*args):
        raise EvaluationQualityError("quality_usage_missing")

    monkeypatch.setattr("app.services.evaluation_jobs.registered_real_runner", failure)
    asyncio.run(execute_job(session.get_bind(), job.id, threading.Event()))
    session.refresh(job)
    assert job.status == "failed" and job.error_code == "quality_usage_missing"
