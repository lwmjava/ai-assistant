"""Request limits cover retries, fallback, terminal failures and durable accounting."""

import asyncio
import json
import threading
from dataclasses import asdict

import httpx
import pytest
from sqlmodel import Session

from app.core.config import settings
from app.llm.mock import MockLLMProvider
from app.llm.routing import FallbackChain, reset_circuits
from app.models.evaluation import EvaluationDataset, EvaluationDatasetVersion, EvaluationJob
from app.models.rag import EmbeddingIndex
from app.models.user import User
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.index_identity import identity_from_provider
from app.rag.service import RAGService
from app.services import evaluation_jobs, evaluation_orchestrator
from app.services.evaluation_datasets import canonical_cases_hash
from app.services.evaluation_jobs import EvaluationJobService, EvaluationJobValidationError, execute_job
from app.services.evaluation_orchestrator import (
    EvaluationOrchestratorError,
    _BudgetedLLM,
    _ExternalBudget,
)
from tests.test_evaluation_adapters import adapter_session as _adapter_session
from tests.test_evaluation_quality import quality_ctx as _quality_ctx


@pytest.fixture
def quality_ctx(monkeypatch):
    yield from _quality_ctx.__wrapped__(monkeypatch)


@pytest.fixture
def adapter_session():
    yield from _adapter_session.__wrapped__()


def _rag_job(quality_ctx, **changes):
    session, user, payload = quality_ctx
    index = session.get(EmbeddingIndex, "idx")
    identity = identity_from_provider(get_embedding_provider())
    for name, value in asdict(identity).items():
        setattr(index, name, value)
    index.identity_key = identity.key()
    session.add(index)
    session.commit()
    payload.update(enable_generation=False, enable_judge=False, max_external_calls=2,
                   max_cost_yuan=0, data_egress_confirmed=True)
    payload.update(changes)
    return EvaluationJobService(session).create_real(user, **payload)[0]


def _transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(handler), trust_env=False, **kw,
    ))


def _empty_store(monkeypatch):
    class Store:
        async def hybrid_search(self, *args, **kwargs):
            return []

    monkeypatch.setattr("app.rag.service.get_vector_store", lambda session: Store())
    monkeypatch.setattr(RAGService, "_cached_backend", lambda self, backend: backend)


@pytest.mark.parametrize("changes", [
    {"max_external_calls": 0}, {"data_egress_confirmed": False},
])
def test_live_retrieval_requires_call_budget_and_egress_confirmation(quality_ctx, monkeypatch, changes):
    _transport(monkeypatch, lambda request: pytest.fail("unauthorized HTTP request"))
    with pytest.raises(EvaluationJobValidationError):
        _rag_job(quality_ctx, **changes)


def test_mock_retrieval_accepts_page_request_ceiling(quality_ctx, monkeypatch):
    from app.rag.embeddings.mock import MockEmbeddingProvider

    monkeypatch.setattr(evaluation_jobs, "embedding_availability", lambda: {"is_mock": True})
    monkeypatch.setattr(evaluation_orchestrator, "get_embedding_provider", lambda: MockEmbeddingProvider())
    job = _rag_job(quality_ctx, max_external_calls=1)
    assert job.max_external_calls == 1 and job.external_calls_used == 0
    budget = _ExternalBudget(1)
    provider = evaluation_orchestrator._budgeted_embedding(budget)
    assert asyncio.run(provider.embed(["synthetic"]))
    assert budget.used == 0


@pytest.mark.parametrize("response", [
    httpx.Response(400, json={"error": "private vendor response"}),
    httpx.Response(200, content=b"not-json"),
    httpx.Response(200, json={"data": []}),
    httpx.Response(200, json=[]),
    httpx.Response(200, json={"data": [None]}),
])
def test_retrieval_external_failure_is_terminal_and_retains_attempt(
    quality_ctx, monkeypatch, response,
):
    session, _, _ = quality_ctx
    job = _rag_job(quality_ctx)
    _empty_store(monkeypatch)
    requests = []

    def handler(request):
        requests.append(request)
        with Session(session.get_bind()) as fresh:
            assert fresh.get(EvaluationJob, job.id).external_calls_used == 1
        return response

    _transport(monkeypatch, handler)
    asyncio.run(execute_job(session.get_bind(), job.id, threading.Event()))
    session.refresh(job)
    assert job.status == "failed"
    assert job.finished_at is not None and job.external_calls_used == 1
    assert len(requests) == 1
    assert job.error_code in {"external_request_failed", "runner_protocol_invalid"}
    assert "private" not in job.error_code


@pytest.mark.parametrize("maximum", [1, 2])
def test_each_fallback_attempt_must_reserve_budget(maximum):
    reset_circuits()
    attempts = []

    class Primary(MockLLMProvider):
        async def chat(self, messages, options=None):
            attempts.append("primary")
            raise httpx.ConnectError("synthetic failure")

    class Secondary(MockLLMProvider):
        async def chat(self, messages, options=None):
            attempts.append("secondary")
            return "answer"

    budget = _ExternalBudget(maximum)
    chain = FallbackChain("chat", [("chat", Primary()), ("fallback", Secondary())])
    if maximum == 1:
        with pytest.raises(EvaluationOrchestratorError, match="external_budget_exhausted"):
            asyncio.run(_BudgetedLLM(chain, budget).chat([]))
        assert attempts == ["primary"] and budget.used == 1
    else:
        assert asyncio.run(_BudgetedLLM(chain, budget).chat([])) == "answer"
        assert attempts == ["primary", "secondary"] and budget.used == 2
    reset_circuits()


@pytest.mark.parametrize("missing", ["budget", "confirmation"])
def test_legacy_queued_retrieval_cannot_bypass_external_guard(quality_ctx, monkeypatch, missing):
    session, _, _ = quality_ctx
    job = _rag_job(quality_ctx)
    if missing == "budget":
        job.max_external_calls = 0
    else:
        options = json.loads(job.options_json)
        options.pop("data_egress_confirmed")
        job.options_json = json.dumps(options)
    session.add(job)
    session.commit()
    _empty_store(monkeypatch)
    _transport(monkeypatch, lambda request: pytest.fail("unapproved legacy HTTP request"))
    asyncio.run(execute_job(session.get_bind(), job.id, threading.Event()))
    session.refresh(job)
    assert job.status == "failed" and job.external_calls_used == 0


def test_embedding_retry_cannot_exceed_persisted_request_budget(quality_ctx, monkeypatch):
    session, _, _ = quality_ctx
    job = _rag_job(quality_ctx, max_external_calls=1)
    _empty_store(monkeypatch)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_RESILIENCE_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "RAG_RETRIEVAL_BACKOFF_BASE_SECONDS", 0)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    _transport(monkeypatch, handler)
    asyncio.run(execute_job(session.get_bind(), job.id, threading.Event()))
    session.refresh(job)
    assert job.status == "failed" and job.external_calls_used == 1
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError, TimeoutError])
def test_agent_failure_keeps_attempt_in_persistent_task(adapter_session, monkeypatch, failure):
    session = adapter_session
    cases = [{"case_id": "one", "query": "synthetic"}]
    session.add(EvaluationDataset(id="count-ds", tenant_id="adapter-a", name="count", slug="count",
                                 family="agent", created_by="platform"))
    session.add(EvaluationDatasetVersion(
        id="count-v", dataset_id="count-ds", tenant_id="adapter-a", version="1",
        status="approved", source="upload", created_by="platform", case_count=1,
        content_hash=canonical_cases_hash(cases), cases_json=json.dumps(cases),
    ))
    session.commit()
    job, _ = EvaluationJobService(session).create_real(
        session.get(User, "platform"), tenant_id="adapter-a", dataset_id="count-ds",
        dataset_version_id="count-v", target_type="agent", target_ids=["system-default"],
        execution_user_id="run-user", enable_generation=False, enable_judge=False,
        idempotency_key="count", max_external_calls=1,
    )

    async def agent(bound_session, user, query, budget):
        budget.consume()
        with Session(session.get_bind()) as fresh:
            assert fresh.get(EvaluationJob, job.id).external_calls_used == 1
        raise failure("synthetic failure")

    monkeypatch.setattr(evaluation_orchestrator, "_run_isolated_agent", agent)
    asyncio.run(execute_job(session.get_bind(), job.id, threading.Event()))
    session.refresh(job)
    assert job.status == ("cancelled" if failure is asyncio.CancelledError else "failed")
    assert job.external_calls_used == 1 and job.finished_at is not None


def test_expected_external_failure_does_not_stop_queue(quality_ctx, monkeypatch, tmp_path):
    session, _, _ = quality_ctx
    first = _rag_job(quality_ctx)
    second = _rag_job(quality_ctx, idempotency_key="second")
    finished = threading.Event()

    async def runner(bound_session, job, marker):
        if job.id == first.id:
            request = httpx.Request("POST", "https://synthetic.invalid/embeddings")
            reply = httpx.Response(400, request=request)
            reply.raise_for_status()
        report = tmp_path / "report.json"
        report.write_text('{"status":"passed"}', encoding="utf-8")
        finished.set()
        return report

    monkeypatch.setattr(evaluation_jobs, "registered_real_runner", runner)
    dispatcher = evaluation_jobs.EvaluationJobDispatcher()
    try:
        dispatcher.submit(session.get_bind(), first.id)
        assert finished.wait(5)
        assert dispatcher._pool is not None
        dispatcher._pool.shutdown(wait=True)
        session.expire_all()
        assert session.get(EvaluationJob, first.id).status == "failed"
        assert session.get(EvaluationJob, second.id).status == "succeeded"
        assert not dispatcher._stopped
    finally:
        dispatcher.shutdown()
