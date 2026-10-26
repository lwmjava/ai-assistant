"""Explicitly registered reports, authorized before file access and projection."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from sqlmodel import Session, col, select

from app.models.evaluation import EvaluationJob
from app.models.user import User
from app.schemas.evaluation import (
    ReportDetail,
    ReportFailure,
    ReportFamily,
    ReportMetric,
    ReportPage,
    ReportSummary,
    ReportTrend,
    TrendPoint,
)


class ReportNotFoundError(Exception):
    """Missing and inaccessible report IDs have the same public result."""


class ReportProtocolError(Exception):
    """The registered file cannot be safely interpreted."""


@dataclass(frozen=True)
class ReportEntry:
    id: str
    family: ReportFamily
    path: str
    scope: str
    tenant_id: str | None = None
    content_hash: str | None = None


REPORT_ROOT = Path(__file__).resolve().parents[2] / "evals" / "reports"
REPORT_ENTRIES = (
    ReportEntry("retrieval-20260919", "retrieval", "rag-v0.1-baseline-20260919.json", "global"),
    ReportEntry("generation-20261009", "generation", "rag-v0.1-gen-real-20261009.json", "global"),
    ReportEntry("agent-20261013", "agent", "agent-chain-v0.1-official-pagt007-20261013.json", "global"),
    ReportEntry("agent-20261014", "agent", "agent-chain-v0.1-official-confirmed-code-r1-20261014.json", "global"),
)

METRICS: dict[str, tuple[str, ...]] = {
    "retrieval": ("recall_at_1", "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10",
                  "quote_coverage", "violation_rate", "latency_p95_ms"),
    "generation": ("answer_point_accuracy", "citation_accuracy", "refusal_correct_rate", "cases_failed",
                   "judge_faithfulness", "judge_correctness", "external_calls_used", "cost_yuan",
                   "recall_at_5", "mrr", "latency_p95_ms", "citation_id_validity"),
    "agent": ("route_accuracy", "followup_recall", "tool_selection_recall", "tool_call_validity_recall",
              "pipeline_completion_rate"),
    "workflow": ("completion_rate", "deterministic_assertion_rate", "latency_p95_ms"),
}
_TOKEN = re.compile(r"[A-Za-z0-9_.:@+\-]{1,128}\Z")


def _token(value: Any) -> str | None:
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else None


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReportProtocolError
    return value


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ReportProtocolError
    return value


def _metric(name: str, value: Any, unit: str = "ratio") -> ReportMetric:
    if value is not None:
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            raise ReportProtocolError
        if unit == "ratio" and value > 1:
            raise ReportProtocolError
    return ReportMetric(name=name, value=value, unit=cast(Any, unit))


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat() if parsed.tzinfo else None
    except ValueError:
        return None


def _failure(row: dict[str, Any], reason: str) -> ReportFailure:
    return ReportFailure(case_id=_token(row.get("case_id")) or "unknown",
                         category=_token(row.get("category")), reason=reason)


def _project(entry: ReportEntry, raw: dict[str, Any]) -> ReportDetail:
    cases = _rows(raw.get("cases"))
    metadata: dict[str, str | int | None] = {}
    failures: list[ReportFailure] = []
    status: Any = "unmeasured"
    recorded_at = None
    if raw.get("report_schema_version") == "real-evaluation-report-1.0.0":
        if raw.get("family") != entry.family:
            raise ReportProtocolError
        raw_metrics = _mapping(raw.get("metrics"))
        units = {"latency_p95_ms": "ms", "cases_failed": "count",
                 "external_calls_used": "count", "cost_yuan": "CNY"}
        metrics = [
            _metric(name, value, units.get(name, "ratio"))
            for name, value in raw_metrics.items()
            if isinstance(name, str) and name in METRICS[entry.family]
        ]
        failures = [
            _failure(row, str(row.get("reason") or "deterministic_assertion_failed"))
            for row in _rows(raw.get("failures"))
        ]
        raw_status = raw.get("status")
        if raw_status not in {"passed", "failed"}:
            raise ReportProtocolError
        status = raw_status
        protocol = "real-evaluation-report-1.0.0"
        recorded_at = _timestamp(raw.get("recorded_at"))
        dataset = _mapping(raw.get("dataset"))
        adapter = _mapping(raw.get("adapter"))
        metadata = {
            "job_id": _token(raw.get("job_id")),
            "target_type": _token(raw.get("target_type")),
            "dataset_version_id": _token(dataset.get("version_id")),
            "dataset_hash": _token(dataset.get("content_hash")),
            "adapter": _token(adapter.get("name")),
            "adapter_version": _token(adapter.get("version")),
            "metric_schema_version": _token(adapter.get("metric_schema_version")),
        }
        quality = _mapping(raw.get("budget", {})).get("quality")
        if isinstance(quality, dict):
            metadata.update({
                "generation_model": _token(quality.get("model")),
                "generation_provider": _token(quality.get("provider")),
                "judge_model": (
                    _token(quality.get("model")) if _mapping(raw.get("options", {})).get("enable_judge") else None
                ),
                "quality_prompt_hash": _token(quality.get("prompt_hash")),
                "judge_evidence": "advisory_same_model",
            })
    elif entry.family == "retrieval":
        if raw.get("report_schema_version") is not None:
            raise ReportProtocolError
        config, overall = _mapping(raw.get("run_config")), _mapping(raw.get("overall"))
        recall = _mapping(overall.get("recall", {}))
        metrics = [_metric(f"recall_at_{k}", recall.get(f"@{k}")) for k in (1, 5, 10)]
        metrics += [_metric("mrr", overall.get("mrr")), _metric("ndcg_at_10", overall.get("ndcg@10")),
                    _metric("quote_coverage", _mapping(overall.get("citation", {})).get("quote_coverage")),
                    _metric("violation_rate", _mapping(overall.get("safety", {})).get("violation_rate")),
                    _metric("latency_p95_ms", _mapping(overall.get("latency_ms", {})).get("p95"), "ms")]
        failures = [_failure(row, "retrieval_failure") for row in _rows(raw.get("failing_cases"))]
        protocol = "retrieval-legacy-0.1"
        recorded_at = _timestamp(config.get("run_at"))
        for key in ("dataset_version", "corpus_version", "git_commit", "embedding_provider",
                    "embedding_model", "mode", "vector_store", "chunk_strategy"):
            metadata[key] = _token(config.get(key))
        dim = config.get("embedding_dim")
        metadata["embedding_dim"] = dim if type(dim) is int and dim > 0 else None
        if failures or (overall.get("safety", {}).get("violation_cases") or 0) > 0:
            status = "failed"
    elif entry.family == "generation":
        if (raw.get("eval") != "rag-v0.1-generation-citation"
                or raw.get("eval_version") != "rag-gen-eval-0.1.0"
                or raw.get("report_schema_version") is not None):
            raise ReportProtocolError
        summary = _mapping(raw.get("summary"))
        publication = _mapping(raw.get("publish_thresholds", {}))
        measured = _mapping(publication.get("measured", {}))
        metrics = [_metric(name, _mapping(measured.get(name, {})).get("value"))
                   for name in METRICS["generation"][:-1]]
        metrics.append(_metric("cases_failed", summary.get("cases_failed"), "count"))
        failures = [_failure(row, "generation_failure") for row in _rows(raw.get("failing_slice"))]
        for row in cases:
            judgment = _mapping(row.get("judgment", {}))
            if (row.get("status") not in (None, "ok")
                    or judgment.get("provisional_status") == "fail"
                    or judgment.get("zero_tolerance_violation") is True):
                failures.append(_failure(row, "generation_failure"))
        for metric_name in METRICS["generation"][:-1]:
            gaps = _mapping(measured.get(metric_name, {})).get("gap_cases", [])
            if not isinstance(gaps, list):
                raise ReportProtocolError
            for gap in gaps:
                if isinstance(gap, str):
                    gap = {"case_id": gap}
                failures.append(_failure(_mapping(gap), metric_name))
        if (failures or summary.get("cases_failed") or summary.get("zero_tolerance_violations")
                or any(_mapping(measured.get(name, {})).get("pass") is False
                       for name in METRICS["generation"][:-1])):
            status = "failed"
        elif publication.get("overall_pass") is False:
            status = "failed"
        elif publication.get("overall_pass") is True:
            status = "passed"
        protocol = "rag-gen-eval-0.1.0"
        recorded_at = _timestamp(raw.get("run_at"))
        for key, value in {
            "dataset_version": _mapping(raw.get("dataset")).get("dataset_version"),
            "dataset_hash": _mapping(raw.get("dataset")).get("dataset_sha256_16"),
            "provider": _mapping(raw.get("model")).get("provider"),
            "model": _mapping(raw.get("model")).get("model"),
            "prompt_hash": _mapping(raw.get("prompt", {})).get("prompt_sha256_16"),
            "git_commit": raw.get("git_commit"), "mode": raw.get("mode"),
            "threshold_version": publication.get("threshold_version"),
        }.items():
            metadata[key] = _token(value)
    else:
        schema = raw.get("report_schema_version")
        evaluator = raw.get("evaluator_version")
        if schema not in (None, "agent-chain-report-1.0.0", "agent-chain-report-2.0.0"):
            raise ReportProtocolError
        expected_evaluators = {
            None: ("agent-chain-eval-0.1.0", "agent-chain-eval-0.2.0"),
            "agent-chain-report-1.0.0": ("agent-chain-eval-0.2.0",),
            "agent-chain-report-2.0.0": ("agent-chain-eval-0.3.0",),
        }
        if evaluator not in expected_evaluators[schema]:
            raise ReportProtocolError
        metrics = [_metric(name, raw.get(name)) for name in METRICS["agent"]]
        for row in cases:
            if row.get("passed") is not None and type(row["passed"]) is not bool:
                raise ReportProtocolError
            if row.get("passed") is False:
                failures.append(_failure(row, "agent_case_failed"))
            record = _mapping(row.get("tool_record", {}))
            if record.get("error_code"):
                failures.append(_failure(row, "agent_tool_failed"))
        status = "failed" if failures or any(m.value is not None and m.value < 1 for m in metrics) else "unmeasured"
        # Per-case success is not an official gate verdict.
        protocol = schema or f"legacy-{evaluator}"
        for key in ("dataset_version", "dataset_hash", "prompt_version", "prompt_hash", "git_commit",
                    "provider", "model", "mode", "evaluator_version", "gate_version"):
            metadata[key] = _token(raw.get(key))
    failures = list({(row.case_id, row.category, row.reason): row for row in failures}.values())
    return ReportDetail(id=entry.id, family=entry.family, scope=cast(Any, entry.scope),
                        status=status, protocol=protocol, recorded_at=recorded_at,
                        metadata=metadata, metrics=metrics, failures=failures[:200],
                        failure_count=len(failures))


class ReportService:
    """Read bounded files only after server-side ownership authorization."""

    def __init__(self, root: Path, entries: tuple[ReportEntry, ...]):
        self.root = root.resolve()
        self.entries = entries
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError("duplicate report ID")

    @staticmethod
    def _allowed(user: User, entry: ReportEntry) -> bool:
        if not user.is_active or entry.scope not in {"global", "tenant"}:
            return False
        return user.role == "system_admin"

    def detail(self, user: User, report_id: str) -> ReportDetail:
        entry = next((e for e in self.entries if e.id == report_id and self._allowed(user, e)), None)
        if entry is None:
            raise ReportNotFoundError
        try:
            path = (self.root / entry.path).resolve()
            if not path.is_relative_to(self.root) or path.suffix != ".json":
                raise ReportProtocolError
            with path.open("rb") as stream:
                content = stream.read(16 * 1024 * 1024 + 1)
            if len(content) > 16 * 1024 * 1024:
                raise ReportProtocolError
            if entry.content_hash and hashlib.sha256(content).hexdigest() != entry.content_hash:
                raise ReportProtocolError
            raw = json.loads(content)
            return _project(entry, _mapping(raw))
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError) as exc:
            raise ReportProtocolError from exc

    def list_reports(self, user: User, family: ReportFamily | None = None,
                     page: int = 1, page_size: int = 20) -> ReportPage:
        allowed = [e for e in self.entries if self._allowed(user, e) and (family is None or e.family == family)]
        items = []
        for entry in allowed[(page - 1) * page_size:page * page_size]:
            try:
                detail = self.detail(user, entry.id)
                item = ReportSummary(**detail.model_dump(exclude={"failures", "failure_count"}))
            except (ReportProtocolError, OSError) as exc:
                item = ReportSummary(id=entry.id, family=entry.family, scope=cast(Any, entry.scope),
                                     status="unavailable" if isinstance(exc, OSError) else "unsupported")
            items.append(item)
        return ReportPage(items=items, total=len(allowed), page=page, page_size=page_size)

    def trend(self, user: User, family: ReportFamily, metric: str) -> ReportTrend:
        if metric not in METRICS[family]:
            raise ReportProtocolError
        reports = self.list_reports(user, family, page_size=len(self.entries) or 1).items
        points = [TrendPoint(report_id=r.id, recorded_at=r.recorded_at,
                             value=next((m.value for m in r.metrics if m.name == metric), None),
                             status=r.status, protocol=r.protocol, metadata=r.metadata) for r in reports]
        # Preserve registry order for undated legacy reports, rather than invent timestamps.
        return ReportTrend(family=family, metric=metric, points=points)


def get_report_service(session: Session | None = None) -> ReportService:
    entries = list(REPORT_ENTRIES)
    if session is not None:
        jobs = session.exec(
            select(EvaluationJob).where(
                col(EvaluationJob.status) == "succeeded",
                col(EvaluationJob.report_id).is_not(None),
                col(EvaluationJob.report_path).is_not(None),
            )
        ).all()
        for job in jobs:
            path = Path(job.report_path or "")
            if path.parent.as_posix() != "evals/reports" or not job.report_id:
                continue
            entries.append(
                ReportEntry(
                    id=job.report_id,
                    family=cast(ReportFamily, job.runner),
                    path=path.name,
                    scope="tenant",
                    tenant_id=job.tenant_id,
                    content_hash=job.report_hash,
                )
            )
    return ReportService(REPORT_ROOT, tuple(entries))
