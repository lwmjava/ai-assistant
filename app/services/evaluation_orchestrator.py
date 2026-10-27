"""Unified real-resource evaluation orchestration with per-case evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.agents.pipeline import AgentPipeline, AgentState
from app.agents.tools.base import Tool, ToolRegistry
from app.core import log_context
from app.llm.base import ChatMessage, LLMOptions, LLMProvider
from app.llm.factory import llm_availability
from app.llm.routing import FallbackChain
from app.models.evaluation import EvaluationCaseResult, EvaluationDatasetVersion, EvaluationJob
from app.models.rag import DocumentChunk
from app.models.user import Tenant, User
from app.models.workflow import Workflow
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.rag.resilience import Deadline
from app.rag.service import RAGService
from app.services.chat_retrieval import ChatRetriever
from app.services.chat_service import ChatService
from app.services.chat_skills import make_skill_runtime
from app.services.evaluation_adapters import builtin_evaluation_adapters
from app.services.evaluation_quality import QualityEmbeddingProvider, QualitySession, parse_judgment
from app.workflow.bridge import WorkflowBridge
from app.workflow.engine import WorkflowEngine


class EvaluationOrchestratorError(RuntimeError):
    """The snapshotted real-resource run cannot be completed safely."""


@dataclass
class _ExternalBudget:
    maximum: int
    used: int = 0
    check: Callable[[], None] | None = None
    record: Callable[[int], None] | None = None
    egress_confirmed: bool = True

    def consume(self) -> None:
        if self.check is not None:
            self.check()
        if not self.egress_confirmed:
            raise EvaluationOrchestratorError("external_data_confirmation_required")
        if self.used >= self.maximum:
            raise EvaluationOrchestratorError("external_budget_exhausted")
        self.used += 1
        if self.record is not None:
            self.record(self.used)


def _request_budget(session: Session, job: EvaluationJob, cancelled: threading.Event) -> _ExternalBudget:
    def record(used: int) -> None:
        job.external_calls_used = used
        session.add(job)
        session.commit()

    return _ExternalBudget(
        maximum=job.max_external_calls, used=job.external_calls_used,
        check=lambda: _validate_job(session, job, cancelled), record=record,
        egress_confirmed=(job.target_type != "rag" or bool(json.loads(job.options_json).get("data_egress_confirmed"))),
    )


class _BudgetedEmbedding(OpenAICompatibleEmbeddingProvider):
    def __init__(self, provider: OpenAICompatibleEmbeddingProvider, budget: _ExternalBudget):
        super().__init__(provider.base_url, provider.api_key, provider.model, provider.dim,
                         provider.timeout, provider.batch_size, provider.input_policy)
        self._budget = budget

    async def _post_batch(self, client, batch: list[str], deadline: Deadline) -> list[list[float]]:
        self._budget.consume()
        try:
            return await super()._post_batch(client, batch, deadline)
        except AttributeError as exc:
            raise EvaluationOrchestratorError("runner_protocol_invalid") from exc


def _budgeted_embedding(budget: _ExternalBudget) -> EmbeddingProvider:
    provider = get_embedding_provider()
    if isinstance(provider, OpenAICompatibleEmbeddingProvider):
        return _BudgetedEmbedding(provider, budget)
    from app.rag.embeddings.mock import MockEmbeddingProvider

    if not isinstance(provider, MockEmbeddingProvider):
        raise EvaluationOrchestratorError("embedding_provider_not_supported")
    return provider


def _validate_job(session: Session, job: EvaluationJob, cancelled: threading.Event) -> None:
    if cancelled.is_set():
        raise asyncio.CancelledError
    with Session(session.get_bind()) as fresh:
        current = fresh.get(EvaluationJob, job.id)
        if current is None or current.status in {"cancel_requested", "cancelled"}:
            raise asyncio.CancelledError
        options = json.loads(job.options_json)
        actor = fresh.get(User, job.submitted_by)
        if (actor is None or not actor.is_active or actor.must_change_password
                or actor.role != "system_admin"
                or actor.tenant_id != options.get("initiator_tenant_id")
                or actor.token_version != options.get("initiator_token_version")):
            raise EvaluationOrchestratorError("initiator_changed")
        try:
            snapshot = builtin_evaluation_adapters.get(job.target_type or "").snapshot(
                fresh, tenant_id=job.tenant_id, target_ids=json.loads(job.target_ids_json),
                execution_user_id=job.execution_user_id,
            )
        except (ValueError, LookupError) as exc:
            raise EvaluationOrchestratorError("target_snapshot_changed") from exc
        frozen = json.loads(job.target_snapshot_json)
        if (snapshot.targets != frozen.get("targets")
                or snapshot.execution_principal != frozen.get("execution_principal")):
            raise EvaluationOrchestratorError("target_snapshot_changed")


async def _guarded_call(call: Awaitable[Any], budget: _ExternalBudget) -> Any:
    task = asyncio.ensure_future(call)
    try:
        while not task.done():
            if budget.check is not None:
                budget.check()
            await asyncio.wait({task}, timeout=0.05)
        result = task.result()
        if budget.check is not None:
            budget.check()
        return result
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class _BudgetedLLM(LLMProvider):
    def __init__(self, delegate: LLMProvider, budget: _ExternalBudget) -> None:
        self._owns_attempt = not isinstance(delegate, FallbackChain)
        if isinstance(delegate, FallbackChain):
            delegate = FallbackChain(delegate.task, [
                (name, _BudgetedLLM(provider, budget)) for name, provider in delegate.hops
            ])
        self._delegate = delegate
        self._budget = budget
        self.model = delegate.model

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> str:
        if self._owns_attempt:
            self._budget.consume()
        return await _guarded_call(self._delegate.chat(messages, options), self._budget)

    async def stream_chat(
        self, messages: list[ChatMessage], options: LLMOptions | None = None
    ) -> AsyncIterator[str]:
        if self._owns_attempt:
            self._budget.consume()
        async for chunk in self._delegate.stream_chat(messages, options):
            yield chunk


@dataclass(frozen=True)
class _AgentOutcome:
    answer: str
    route: str
    tools: list[str]
    error_code: str | None
    elapsed_ms: float


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 3)


def _load_cases(session: Session, job: EvaluationJob) -> list[dict[str, Any]]:
    version = session.get(EvaluationDatasetVersion, job.dataset_version_id)
    if version is None or version.tenant_id != job.tenant_id or version.content_hash != job.input_hash:
        raise EvaluationOrchestratorError("dataset_snapshot_changed")
    try:
        cases = json.loads(version.cases_json)
    except (TypeError, ValueError) as exc:
        raise EvaluationOrchestratorError("dataset_invalid") from exc
    if not isinstance(cases, list) or not all(isinstance(item, dict) for item in cases):
        raise EvaluationOrchestratorError("dataset_invalid")
    return cases


async def _run_rag(
    session: Session,
    job: EvaluationJob,
    cases: list[dict[str, Any]],
    cancelled: threading.Event,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    reciprocal_ranks: list[float] = []
    latencies: list[float] = []
    hits_at = {1: 0, 5: 0, 10: 0}
    options = json.loads(job.options_json)
    quality = QualitySession(session, job, cancelled) if options.get("enable_generation") else None
    budget = _request_budget(session, job, cancelled)
    rag = (
        RAGService(session, job.tenant_id, embedding_provider=QualityEmbeddingProvider(quality))
        if quality is not None else RAGService(
            session, job.tenant_id, embedding_provider=_budgeted_embedding(budget),
        )
    )
    judgments: list[dict[str, float | None]] = []
    if quality is not None and not 1 <= len(cases) <= 10:
        raise EvaluationOrchestratorError("quality_case_limit")
    for case in cases:
        _validate_job(session, job, cancelled)
        case_id = str(case.get("case_id") or "")
        query = str(case.get("query") or "").strip()
        expected = {str(value) for value in case.get("expected_document_ids", [])}
        if not case_id or not query or not expected:
            raise EvaluationOrchestratorError("rag_case_invalid")
        started = perf_counter()
        calls_before = job.external_calls_used
        hits = await _guarded_call(rag.search(query, top_k=10), budget)
        _validate_job(session, job, cancelled)
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        latencies.append(elapsed_ms)
        ranked_documents = [hit.document_id for hit in hits]
        rank = next(
            (index for index, document_id in enumerate(ranked_documents, 1) if document_id in expected),
            None,
        )
        reciprocal_rank = 0.0 if rank is None else 1.0 / rank
        reciprocal_ranks.append(reciprocal_rank)
        for cutoff in hits_at:
            hits_at[cutoff] += int(rank is not None and rank <= cutoff)
        passed = rank is not None and rank <= 5
        evidence = {
            "expected_document_ids": sorted(expected),
            "retrieved": [
                {
                    "rank": index,
                    "chunk_id": hit.id,
                    "document_id": hit.document_id,
                    "score": hit.score,
                    "similarity": hit.similarity,
                }
                for index, hit in enumerate(hits, 1)
            ],
        }
        metrics = {
            "first_relevant_rank": rank,
            "reciprocal_rank": reciprocal_rank,
            "hit_at_5": passed,
            "latency_ms": elapsed_ms,
        }
        assertions = [{"name": "expected_document_in_top_5", "passed": passed}]
        if quality is not None:
            selected = set(json.loads(job.target_ids_json))
            frozen = json.loads(job.target_snapshot_json)["targets"]
            index_ids = {item["metadata"]["index_id"] for item in frozen}
            sources = []
            for hit in hits:
                if hit.document_id not in selected:
                    continue
                chunk = session.get(DocumentChunk, hit.id, populate_existing=True)
                if (chunk is None or chunk.tenant_id != job.tenant_id
                        or chunk.document_id != hit.document_id or chunk.index_id not in index_ids):
                    raise EvaluationOrchestratorError("quality_chunk_changed")
                sources.append({"chunk_id": chunk.id, "content": chunk.content})
            answer = await quality.call("generation", {"question": query, "evidence": sources})
            citations = re.findall(r"\[\[([^\[\]]+)\]\]", answer)
            citation_ids = {item["chunk_id"] for item in sources}
            citations_valid = all(item in citation_ids for item in citations) and (
                bool(citations) if sources else not citations
            )
            metrics["citations_valid"] = citations_valid
            evidence["generation"] = {
                "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                "cited_chunk_ids": [item for item in citations if item in citation_ids],
                "unknown_citation_count": sum(item not in citation_ids for item in citations),
                "advisory_judge": True,
            }
            passed = passed and citations_valid
            assertions.append({"name": "generation_citations_known", "passed": citations_valid})
            if options.get("enable_judge"):
                reference = case.get("expected_answer_points", [])
                judgment = parse_judgment(await quality.call("judge", {
                    "question": query, "evidence": sources, "answer": answer,
                    "reference": reference,
                }), has_reference=bool(reference))
                judgments.append(judgment)
                metrics.update({f"judge_{name}": score for name, score in judgment.items()})
            elapsed_ms = round((perf_counter() - started) * 1000, 3)
        result = EvaluationCaseResult(
            job_id=job.id,
            tenant_id=job.tenant_id,
            case_id=case_id,
            status="passed" if passed else "failed",
            metrics_json=json.dumps(metrics, ensure_ascii=False, sort_keys=True),
            assertions_json=json.dumps(
                assertions,
                ensure_ascii=False,
                sort_keys=True,
            ),
            evidence_json=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            elapsed_ms=round(elapsed_ms),
            external_calls_used=job.external_calls_used - calls_before,
        )
        session.add(result)
        session.commit()
        rows.append(
            {
                "case_id": case_id,
                "status": result.status,
                "metrics": metrics,
                "evidence": evidence,
            }
        )
        job.case_completed = len(rows)
        session.add(job)
        session.commit()
    count = len(rows)
    metrics = {
        f"recall_at_{cutoff}": hits_at[cutoff] / count for cutoff in sorted(hits_at)
    }
    metrics.update(
        {
            "mrr": sum(reciprocal_ranks) / count,
            "latency_p95_ms": _percentile(latencies, 0.95),
        }
    )
    if quality is not None:
        metrics["citation_id_validity"] = sum(
            bool(row["metrics"].get("citations_valid")) for row in rows
        ) / count
        metrics["external_calls_used"] = job.external_calls_used
        for name in ("faithfulness", "correctness"):
            scores = [value for item in judgments if (value := item[name]) is not None]
            metrics[f"judge_{name}"] = sum(scores) / len(scores) if scores else None
        metrics["cost_yuan"] = float(sum(
            float(item["cost_yuan"]) for item in quality.calls
        ))
    return {"metrics": metrics, "cases": rows}


async def _run_isolated_agent(
    session: Session,
    user: User,
    query: str,
    budget: _ExternalBudget,
) -> _AgentOutcome:
    """Run the production AgentPipeline without conversation/state persistence or write tools."""
    if not user.is_active or user.must_change_password:
        raise EvaluationOrchestratorError("execution_user_unavailable")
    if llm_availability() != "real":
        raise EvaluationOrchestratorError("real_llm_unavailable")
    production = ChatService()
    class EvaluationChatService(ChatService):
        def _build_retriever(self, session: Session, user: User):
            from app.core.config import settings

            if not settings.RAG_ENABLED:
                return None
            reader = User(
                id=user.id, tenant_id=user.tenant_id, username=user.username, hashed_password="",
                role=user.role, token_version=user.token_version, is_active=user.is_active,
                must_change_password=user.must_change_password,
            )
            return ChatRetriever(lambda: RAGService(
                session, reader.tenant_id, embedding_provider=_budgeted_embedding(budget), reader=reader,
            ).make_retriever())

    service = EvaluationChatService(
        _BudgetedLLM(production.llm, budget),
        intent_llm_provider=_BudgetedLLM(production._intent_llm(), budget),
    )
    available = await production._build_tools()
    def guarded_tool(tool: Tool) -> Tool:
        async def execute(arguments: dict) -> str:
            budget.consume()
            return await _guarded_call(tool.execute(arguments), budget)
        return Tool(tool.name, tool.description, tool.parameters, execute, tool.contract)

    safe_tools = ToolRegistry(
        [
            guarded_tool(tool)
            for tool in available.all()
            if tool.contract is not None and tool.contract.risk.value == "L0"
        ]
    )
    state = AgentState(user_input=query, retrieval_query=query)
    previous_user = log_context.get_user_id()
    previous_tenant = log_context.get_tenant_id()
    started = perf_counter()
    try:
        log_context.set_user(user.id, user.tenant_id)
        skill_ctx = await service._match_skills_async(session, user, query)
        skill_runtime = make_skill_runtime(session, user, skill_ctx, safe_tools)
        def prepare_pipeline(pipeline):
            if isinstance(pipeline, AgentPipeline):
                pipeline.authority_check = budget.check
                pipeline.execution_principal = (user.id, user.tenant_id)
            else:
                service._wire_supervisor_tools(session, user, pipeline)

        result = await _guarded_call(service._run_selected_agent(
            session, user, state, service._build_retriever(session, user),
            safe_tools, skill_ctx, skill_runtime, prepare_pipeline=prepare_pipeline,
        ), budget)
    finally:
        log_context.set_user(previous_user, previous_tenant)
    tools = []
    for observation in result.tool_results:
        match = re.match(r"^\[([^\]]+)\]", str(observation))
        if match:
            tools.append(match.group(1))
    return _AgentOutcome(
        answer=result.answer,
        route="full" if result.needs_full_pipeline else "short",
        tools=tools,
        error_code=result.error_code,
        elapsed_ms=round((perf_counter() - started) * 1000, 3),
    )


def _persist_case(
    session: Session,
    job: EvaluationJob,
    *,
    case_id: str,
    passed: bool,
    metrics: dict[str, Any],
    assertions: list[dict[str, Any]],
    evidence: dict[str, Any],
    elapsed_ms: float,
    external_calls_used: int,
) -> dict[str, Any]:
    result = EvaluationCaseResult(
        job_id=job.id,
        tenant_id=job.tenant_id,
        case_id=case_id,
        status="passed" if passed else "failed",
        metrics_json=json.dumps(metrics, ensure_ascii=False, sort_keys=True),
        assertions_json=json.dumps(assertions, ensure_ascii=False, sort_keys=True),
        evidence_json=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        elapsed_ms=round(elapsed_ms),
        external_calls_used=external_calls_used,
    )
    session.add(result)
    job.case_completed += 1
    session.add(job)
    session.commit()
    return {
        "case_id": case_id,
        "status": result.status,
        "metrics": metrics,
        "assertions": assertions,
        "evidence": evidence,
    }


async def _run_agent(
    session: Session,
    job: EvaluationJob,
    cases: list[dict[str, Any]],
    cancelled: threading.Event,
) -> dict[str, Any]:
    user = session.get(User, job.execution_user_id or "")
    if user is None or user.tenant_id != job.tenant_id:
        raise EvaluationOrchestratorError("execution_user_unavailable")
    try:
        principal = json.loads(job.execution_principal_json or "{}")
    except (TypeError, ValueError) as exc:
        raise EvaluationOrchestratorError("execution_principal_invalid") from exc
    if not isinstance(principal, dict) or (
        principal.get("id"),
        principal.get("username"),
        principal.get("role"),
        principal.get("token_version"),
    ) != (user.id, user.username, user.role, user.token_version):
        raise EvaluationOrchestratorError("execution_principal_changed")
    budget = _request_budget(session, job, cancelled)
    rows: list[dict[str, Any]] = []
    route_hits = 0
    route_expected = 0
    tool_expected = 0
    tool_hits = 0
    valid_runs = 0
    for case in cases:
        _validate_job(session, job, cancelled)
        case_id = str(case.get("case_id") or "")
        query = str(case.get("query") or case.get("user_input") or "").strip()
        if not case_id or not query:
            raise EvaluationOrchestratorError("agent_case_invalid")
        before = budget.used
        outcome = await _run_isolated_agent(session, user, query, budget)
        expected_route = case.get("expected_route")
        expected_tools = {str(item) for item in case.get("expected_tools", [])}
        route_ok = expected_route is None or outcome.route == expected_route
        tools_ok = expected_tools.issubset(set(outcome.tools))
        run_ok = outcome.error_code is None and bool(outcome.answer.strip())
        if expected_route is not None:
            route_expected += 1
            route_hits += int(route_ok)
        if expected_tools:
            tool_expected += 1
            tool_hits += int(tools_ok)
        valid_runs += int(run_ok)
        assertions = [
            {"name": "pipeline_completed", "passed": run_ok},
        ]
        if expected_route is not None:
            assertions.append({"name": "route", "passed": route_ok})
        if "expected_tools" in case:
            assertions.append({"name": "expected_tools", "passed": tools_ok})
        rows.append(
            _persist_case(
                session,
                job,
                case_id=case_id,
                passed=all(item["passed"] for item in assertions),
                metrics={"route_match": route_ok if expected_route is not None else None,
                         "tool_match": tools_ok if "expected_tools" in case else None,
                         "pipeline_completed": run_ok},
                assertions=assertions,
                evidence={
                    "route": outcome.route,
                    "tools": outcome.tools,
                    "answer_present": bool(outcome.answer.strip()),
                    "error_code": outcome.error_code,
                },
                elapsed_ms=outcome.elapsed_ms,
                external_calls_used=budget.used - before,
            )
        )
    count = len(rows)
    return {
        "metrics": {
            "route_accuracy": route_hits / route_expected if route_expected else None,
            "followup_recall": None,
            "tool_selection_recall": tool_hits / tool_expected if tool_expected else None,
            "tool_call_validity_recall": None,
            "pipeline_completion_rate": valid_runs / count,
        },
        "cases": rows,
    }


class _IsolatedWorkflowBridge(WorkflowBridge):
    def __init__(
        self, production_session: Session, production_owner: User, budget: _ExternalBudget
    ) -> None:
        self.production_session = production_session
        self.production_owner = production_owner
        self.budget = budget
        self.last_outcome: _AgentOutcome | None = None

    async def execute(self, session: Session, owner: User, prompt: str) -> str:
        del session, owner
        self.last_outcome = await _run_isolated_agent(
            self.production_session, self.production_owner, prompt, self.budget
        )
        if self.last_outcome.error_code:
            raise EvaluationOrchestratorError(self.last_outcome.error_code)
        return self.last_outcome.answer


async def _run_workflow(
    session: Session,
    job: EvaluationJob,
    cases: list[dict[str, Any]],
    cancelled: threading.Event,
) -> dict[str, Any]:
    target_ids = json.loads(job.target_ids_json)
    workflow = session.get(Workflow, target_ids[0] if target_ids else "")
    if workflow is None or workflow.tenant_id != job.tenant_id:
        raise EvaluationOrchestratorError("target_unavailable")
    owner = session.get(User, workflow.owner_id)
    tenant = session.get(Tenant, job.tenant_id)
    if owner is None or tenant is None or not owner.is_active or not tenant.is_active:
        raise EvaluationOrchestratorError("execution_user_unavailable")
    budget = _request_budget(session, job, cancelled)
    isolated_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(isolated_engine)
    rows: list[dict[str, Any]] = []
    completed = 0
    assertions_passed = 0
    latencies: list[float] = []
    try:
        with Session(isolated_engine) as isolated:
            isolated.add(Tenant(id=tenant.id, name=tenant.name, is_active=True))
            isolated.add(
                User(
                    id=owner.id,
                    tenant_id=owner.tenant_id,
                    username=f"eval-{owner.id}",
                    hashed_password="",
                    role=owner.role,
                    is_active=True,
                )
            )
            isolated_workflow = Workflow(
                id=workflow.id,
                tenant_id=workflow.tenant_id,
                owner_id=workflow.owner_id,
                name=workflow.name,
                cron_expr=workflow.cron_expr,
                prompt_template=workflow.prompt_template,
                timezone=workflow.timezone,
                enabled=False,
                webhook_url=None,
                timeout_seconds=workflow.timeout_seconds,
            )
            isolated.add(isolated_workflow)
            isolated.commit()
            bridge = _IsolatedWorkflowBridge(session, owner, budget)
            engine = WorkflowEngine(bridge=bridge)
            for case in cases:
                _validate_job(session, job, cancelled)
                case_id = str(case.get("case_id") or "")
                query = str(case.get("query") or "").strip()
                if not case_id or not query:
                    raise EvaluationOrchestratorError("workflow_case_invalid")
                prompt = workflow.prompt_template.replace("{input}", query)
                if "{input}" not in workflow.prompt_template:
                    prompt = f"{workflow.prompt_template}\n\n评测输入：{query}"
                before = budget.used
                started = perf_counter()
                isolated_owner = isolated.get(User, owner.id)
                if isolated_owner is None:
                    raise EvaluationOrchestratorError("execution_user_unavailable")
                execution = await engine.execute(
                    isolated, isolated_workflow, isolated_owner, prompt_override=prompt
                )
                elapsed_ms = round((perf_counter() - started) * 1000, 3)
                latencies.append(elapsed_ms)
                answer = execution.output or ""
                required = [str(item) for item in case.get("expected_answer_points", [])]
                forbidden = [str(item) for item in case.get("forbidden_answer_points", [])]
                content_ok = all(item in answer for item in required) and not any(
                    item in answer for item in forbidden
                )
                run_ok = execution.status == "success"
                completed += int(run_ok)
                assertions_passed += int(content_ok)
                assertions = [
                    {"name": "workflow_completed", "passed": run_ok},
                    {"name": "deterministic_content", "passed": content_ok},
                ]
                rows.append(
                    _persist_case(
                        session,
                        job,
                        case_id=case_id,
                        passed=run_ok and content_ok,
                        metrics={"workflow_completed": run_ok, "deterministic_content": content_ok},
                        assertions=assertions,
                        evidence={
                            "isolated": True,
                            "webhook_suppressed": True,
                            "execution_status": execution.status,
                            "answer_present": bool(answer.strip()),
                        },
                        elapsed_ms=elapsed_ms,
                        external_calls_used=budget.used - before,
                    )
                )
    finally:
        isolated_engine.dispose()
    count = len(rows)
    return {
        "metrics": {
            "completion_rate": completed / count,
            "deterministic_assertion_rate": assertions_passed / count,
            "latency_p95_ms": _percentile(latencies, 0.95),
        },
        "cases": rows,
    }


async def run_real_evaluation(
    session: Session,
    job: EvaluationJob,
    cancelled: threading.Event,
    report_path: Path,
) -> Path:
    if job.job_kind != "real_resource" or job.mode != "real":
        raise EvaluationOrchestratorError("real_job_required")
    _validate_job(session, job, cancelled)
    cases = _load_cases(session, job)
    if job.target_type == "rag":
        result = await _run_rag(session, job, cases, cancelled)
        family = "generation" if json.loads(job.options_json).get("enable_generation") else "retrieval"
    elif job.target_type == "agent":
        result = await _run_agent(session, job, cases, cancelled)
        family = "agent"
    elif job.target_type == "workflow":
        result = await _run_workflow(session, job, cases, cancelled)
        family = "workflow"
    else:
        raise EvaluationOrchestratorError("unsupported_target_type")
    _validate_job(session, job, cancelled)
    failures = [
        {"case_id": row["case_id"], "reason": "deterministic_assertion_failed"}
        for row in result["cases"]
        if row["status"] != "passed"
    ]
    payload = {
        "report_schema_version": "real-evaluation-report-1.0.0",
        "recorded_at": datetime.now(UTC).isoformat(),
        "job_id": job.id,
        "tenant_id": job.tenant_id,
        "family": family,
        "target_type": job.target_type,
        "adapter": {
            "name": job.adapter_name,
            "version": job.adapter_version,
            "metric_schema_version": job.metric_schema_version,
        },
        "target_snapshot": json.loads(job.target_snapshot_json),
        "dataset": {
            "id": job.dataset_id,
            "version_id": job.dataset_version_id,
            "content_hash": job.input_hash,
        },
        "options": json.loads(job.options_json),
        "budget": json.loads(job.budget_json),
        "metrics": result["metrics"],
        "cases": result["cases"],
        "failures": failures,
        "status": "passed" if not failures else "failed",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path
