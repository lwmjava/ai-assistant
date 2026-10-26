"""Read-only report authorization, protocol and redaction contracts."""

import json
from pathlib import Path

import pytest

from app.models.user import User
from app.services.evaluation_reports import (
    ReportEntry,
    ReportNotFoundError,
    ReportProtocolError,
    ReportService,
)


def principal(role="system_admin", tenant="a"):
    return User(id="reader", tenant_id=tenant, username="reader", hashed_password="", role=role)


@pytest.fixture
def reports(tmp_path):
    raw = {
        "run_config": {"dataset_version": "0.1.0", "mode": "official"},
        "overall": {"recall": {"@5": 0.75}, "mrr": None},
        "cases": [],
        "failing_cases": [{"case_id": "rag-001", "category": "fact", "failures": ["secret"],
                           "query": "private input", "answer": "private answer"}],
    }
    (tmp_path / "report.json").write_text(json.dumps(raw), encoding="utf-8")
    entries = (
        ReportEntry("global-1", "retrieval", "report.json", "global"),
        ReportEntry("tenant-a", "retrieval", "report.json", "tenant", "a"),
        ReportEntry("tenant-b", "retrieval", "report.json", "tenant", "b"),
        ReportEntry("unknown", "retrieval", "report.json", "unknown"),
    )
    return ReportService(tmp_path, entries)


@pytest.mark.parametrize("role,ids", [
    ("system_admin", ["global-1", "tenant-a", "tenant-b"]), ("system_viewer", []),
    ("tenant_admin", []), ("member", []), ("viewer", []),
])
def test_domains_are_filtered_before_counting(reports, role, ids):
    page = reports.list_reports(principal(role), page=1, page_size=20)
    assert [r.id for r in page.items] == ids
    assert page.total == len(ids)


@pytest.mark.parametrize("report_id", ["tenant-b", "global-1", "unknown", "absent"])
def test_denied_and_missing_are_indistinguishable(reports, report_id):
    with pytest.raises(ReportNotFoundError):
        reports.detail(principal("tenant_admin"), report_id)


def test_null_zero_failure_and_redaction(reports):
    report = reports.detail(principal(), "global-1")
    assert report.status == "failed"
    metrics = {m.name: m.value for m in report.metrics}
    assert metrics["recall_at_5"] == 0.75
    assert metrics["mrr"] is None
    output = report.model_dump_json()
    for secret in ["private input", "private answer", "secret", "report.json"]:
        assert secret not in output
    assert report.failures[0].case_id == "rag-001"


def test_unknown_protocol_is_explicitly_rejected(reports):
    p = reports.root / "report.json"
    raw = json.loads(p.read_text())
    raw["report_schema_version"] = "future-99"
    p.write_text(json.dumps(raw))
    with pytest.raises(ReportProtocolError):
        reports.detail(principal(), "global-1")
    assert reports.list_reports(principal()).items[0].status == "unsupported"


def test_traversal_cannot_read_outside_report_root(tmp_path):
    service = ReportService(tmp_path, (ReportEntry("escape", "retrieval", "../private.json", "global"),))
    with pytest.raises(ReportProtocolError):
        service.detail(principal(), "escape")


def test_unauthorized_report_is_never_read(reports, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unauthorized file read")
    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(ReportNotFoundError):
        reports.detail(principal("tenant_admin"), "tenant-b")


def test_real_reports_match_source_and_hide_payloads():
    from app.services.evaluation_reports import get_report_service

    service = get_report_service()
    rows = service.list_reports(principal()).items
    assert {r.family for r in rows} == {"retrieval", "generation", "agent"}
    generation = service.detail(principal(), "generation-20261009")
    assert generation.status == "failed"
    assert {m.name: m.value for m in generation.metrics}["answer_point_accuracy"] == 0.8387
    assert "NW-HR-001" not in generation.model_dump_json()
    retrieval = service.detail(principal(), "retrieval-20260919")
    assert {m.name: m.value for m in retrieval.metrics}["recall_at_5"] == 0.9714285714285714


def test_trend_keeps_missing_values_and_does_not_mix_domains(reports):
    trend = reports.trend(principal(), "retrieval", "mrr")
    assert [p.report_id for p in trend.points] == ["global-1", "tenant-a", "tenant-b"]
    assert all(point.value is None for point in trend.points)


@pytest.mark.parametrize("value", [True, -1, 2, "0.8", float("inf")])
def test_malformed_metrics_are_rejected_not_coerced(reports, value):
    p = reports.root / "report.json"
    raw = json.loads(p.read_text())
    raw["overall"]["recall"]["@5"] = value
    p.write_text(json.dumps(raw))
    with pytest.raises(ReportProtocolError):
        reports.detail(principal(), "global-1")


def test_generation_includes_measured_failure_slices():
    from app.services.evaluation_reports import get_report_service

    report = get_report_service().detail(principal(), "generation-20261009")
    assert {"rag-015", "rag-028", "rag-030", "rag-031", "rag-036"} <= {
        row.case_id for row in report.failures
    }


def test_generation_negative_measurement_overrides_contradictory_pass(tmp_path):
    raw = {"eval": "rag-v0.1-generation-citation", "eval_version": "rag-gen-eval-0.1.0",
           "summary": {"cases_failed": 0}, "dataset": {}, "model": {}, "cases": [],
           "failing_slice": [], "publish_thresholds": {
               "overall_pass": True, "measured": {"answer_point_accuracy": {"value": 0.8, "pass": False}}}}
    (tmp_path / "gen.json").write_text(json.dumps(raw))
    service = ReportService(tmp_path, (ReportEntry("gen", "generation", "gen.json", "global"),))
    assert service.detail(principal(), "gen").status == "failed"


def test_legacy_agent_missing_metrics_and_tool_failure(tmp_path):
    raw = {"evaluator_version": "agent-chain-eval-0.2.0", "route_accuracy": 1,
           "cases": [{"case_id": "agent-001", "passed": True,
                      "tool_record": {"error_code": "private error text"}, "raw_reply": "secret"}]}
    (tmp_path / "agent.json").write_text(json.dumps(raw))
    service = ReportService(tmp_path, (ReportEntry("old", "agent", "agent.json", "global"),))
    report = service.detail(principal(), "old")
    assert report.status == "failed"
    assert {m.name: m.value for m in report.metrics}["tool_call_validity_recall"] is None
    assert "private error text" not in report.model_dump_json()
    raw["report_schema_version"] = "agent-chain-report-2.0.0"
    (tmp_path / "agent.json").write_text(json.dumps(raw))
    with pytest.raises(ReportProtocolError):
        service.detail(principal(), "old")


def test_missing_and_corrupt_files_have_honest_list_status(reports):
    p = reports.root / "report.json"
    p.write_text("not JSON")
    assert reports.list_reports(principal()).items[0].status == "unsupported"
    p.unlink()
    assert reports.list_reports(principal()).items[0].status == "unavailable"


@pytest.fixture
def real_report(tmp_path):
    raw = {
        "report_schema_version": "real-evaluation-report-1.0.0", "family": "retrieval",
        "status": "passed", "cases": [], "failures": [], "metrics": {"recall_at_5": 1},
        "dataset": {"version_id": "frozen-version", "content_hash": "frozen-hash"},
        "adapter": {}, "target_snapshot": {"targets": [{"metadata": {
            "embedding_provider": "openai", "embedding_model": "vendor/embedding-model",
            "embedding_dim": 1024, "vector_backend": "milvus", "index_id": "frozen-index",
            "document_body": "private document", "api_key": "private credential",
        }}]},
    }
    path = tmp_path / "real.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    service = ReportService(tmp_path, (ReportEntry("real", "retrieval", "real.json", "global"),))
    return service, path, raw


def test_real_report_history_preserves_frozen_dataset_and_embedding_identity(real_report):
    service, _, _ = real_report
    report = service.detail(principal(), "real")
    metadata = report.metadata
    assert metadata["dataset_version"] == metadata["dataset_version_id"] == "frozen-version"
    assert metadata["embedding_provider"] == "openai"
    assert metadata["embedding_model"] == "vendor/embedding-model"
    assert metadata["embedding_dim"] == 1024
    assert metadata["vector_backend"] == "milvus"
    assert metadata["index_id"] == "frozen-index"
    assert service.trend(principal(), "retrieval", "recall_at_5").points[0].metadata == metadata
    assert "private" not in report.model_dump_json()


@pytest.mark.parametrize("identities", [
    [], [{"metadata": {}}],
    [{"metadata": {"embedding_model": "a", "embedding_dim": 64}},
     {"metadata": {"embedding_model": "b", "embedding_dim": 128}}],
    [{"metadata": {"embedding_model": "invalid\nmodel", "embedding_dim": True}}],
])
def test_real_report_missing_or_mixed_identity_is_not_invented(real_report, identities):
    service, path, raw = real_report
    raw["target_snapshot"]["targets"] = identities
    path.write_text(json.dumps(raw), encoding="utf-8")
    metadata = service.detail(principal(), "real").metadata
    assert metadata["embedding_model"] is None
    assert metadata["embedding_dim"] is None
