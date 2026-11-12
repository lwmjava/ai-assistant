"""五项性能指标压测脚本的门槛、报告与隔离实测。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import Role
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.user import User
from app.rag.embeddings.factory import set_embedding_override
from app.rag.embeddings.mock import MockEmbeddingProvider
from scripts.perf_benchmark import (
    _EXTRACTABLE_PDF_TEXT,
    API_P95_MS_TARGET,
    CONCURRENT_CONVERSATIONS_TARGET,
    FIRST_TOKEN_P95_MS_TARGET,
    PDF_SIZE_BYTES,
    PDF_UPLOAD_SECONDS_TARGET,
    RETRIEVAL_P95_MS_TARGET,
    MetricResult,
    build_sized_pdf,
    collect_machine_specs,
    extract_health_identity,
    finalize_metric,
    measure_concurrency,
    official_reasons,
    p95,
    percentile,
    render_markdown,
    run_benchmark,
    snapshot_health,
    write_report,
)


def test_percentile_linear_interpolation() -> None:
    assert percentile([10], 95) == 10
    assert percentile([1, 2, 3, 4], 0) == 1
    assert percentile([1, 2, 3, 4], 100) == 4
    assert p95([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]) == pytest.approx(19.05)
    with pytest.raises(ValueError):
        percentile([], 95)


def test_build_sized_pdf_keeps_text_and_target_size(tmp_path: Path) -> None:
    payload = build_sized_pdf()
    assert len(payload) == PDF_SIZE_BYTES
    path = tmp_path / "bench.pdf"
    path.write_bytes(payload)
    text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    assert "standard plan price is 199" in text
    assert text.count("X") < 100


def test_official_reasons_reject_mock_and_non_inventory() -> None:
    health = {
        "embedding": {
            "mode": "mock",
            "provider": "mock",
            "model": "mock-embedding",
            "dim": 64,
            "is_mock": True,
        },
        "vector_store": {"backend": "local"},
        "llm": {"mode": "mock"},
    }
    machine = {"matches_inventory": False, "app_process_count": 1, "physical_cores": 6, "ram_gib": 24}
    retrieval = official_reasons(key="retrieval_p95", health=health, machine=machine)
    assert any("Mock" in item for item in retrieval)
    assert any("Milvus" in item for item in retrieval)
    assert any("4C8G" in item for item in retrieval)
    first_token = official_reasons(key="first_token_p95", health=health, machine=machine)
    assert any("LLM" in item for item in first_token)
    api = official_reasons(key="api_response_p95", health=health, machine=machine)
    assert api == ["当前机器不是清单机 4C8G"]


def test_finalize_metric_does_not_treat_mock_as_official_pass() -> None:
    health = {
        "embedding": {"is_mock": True, "provider": "mock", "model": "mock-embedding", "dim": 8},
        "vector_store": {"backend": "local"},
        "llm": {"mode": "mock"},
    }
    machine = {"matches_inventory": True, "app_process_count": 1}
    metric = finalize_metric(
        MetricResult(
            key="retrieval_p95",
            title="知识库检索延迟 (P95)",
            value=12.0,
            unit="ms",
            target=RETRIEVAL_P95_MS_TARGET,
            comparison="<=",
            samples=20,
        ),
        health,
        machine,
    )
    assert metric.meets_target is True
    assert metric.official_evidence is False
    assert any("Mock" in item for item in metric.official_reasons)


def test_official_pass_requires_real_stack_and_target() -> None:
    health = {
        "embedding": {"is_mock": False, "provider": "openai", "model": "text-embedding-v3", "dim": 1024},
        "vector_store": {"backend": "milvus"},
        "llm": {"mode": "real"},
    }
    machine = {"matches_inventory": True, "app_process_count": 1}
    ok = finalize_metric(
        MetricResult(
            key="retrieval_p95",
            title="知识库检索延迟 (P95)",
            value=200.0,
            unit="ms",
            target=RETRIEVAL_P95_MS_TARGET,
            comparison="<=",
            samples=20,
        ),
        health,
        machine,
    )
    assert ok.official_evidence is True
    missed = finalize_metric(
        MetricResult(
            key="retrieval_p95",
            title="知识库检索延迟 (P95)",
            value=400.0,
            unit="ms",
            target=RETRIEVAL_P95_MS_TARGET,
            comparison="<=",
            samples=20,
        ),
        health,
        machine,
    )
    assert missed.meets_target is False
    assert missed.official_evidence is False


def test_report_includes_health_identity_and_machine_specs(tmp_path: Path) -> None:
    report = {
        "title": "五项性能指标隔离压测",
        "started_at": "2026-11-12 01:00:00 (Asia/Shanghai)",
        "finished_at": "2026-11-12 01:01:00 (Asia/Shanghai)",
        "timezone": "Asia/Shanghai",
        "machine": {
            "hostname": "test-host",
            "os": "Windows 10",
            "processor": "test-cpu",
            "logical_processors": 12,
            "physical_cores": 6,
            "ram_gib": 24.0,
            "python": "3.12.0",
            "app_process_count": 1,
            "matches_inventory": False,
        },
        "health": {
            "status": "ok",
            "http_status": 200,
            "env": "development",
            "embedding": {
                "mode": "mock",
                "provider": "mock",
                "model": "mock-embedding",
                "dim": 64,
                "is_mock": True,
                "connectivity_verified": False,
                "quality_evidence": False,
            },
            "vector_store": {"backend": "local", "runtime": ""},
            "llm": {"mode": "mock"},
        },
        "pdf": {
            "file_bytes": PDF_SIZE_BYTES,
            "extractable_text_chars": len(_EXTRACTABLE_PDF_TEXT),
            "note": "填充不进入文本层。",
        },
        "metrics": [
            {
                "title": "API 响应时间 (P95，不含 LLM)",
                "value": 12.3,
                "unit": "ms",
                "target": API_P95_MS_TARGET,
                "comparison": "<=",
                "samples": 20,
                "meets_target": True,
                "official_evidence": False,
                "official_reasons": ["当前机器不是清单机 4C8G"],
                "error": None,
            }
        ],
        "disposition": "本跑不能作为上线验收证据。",
    }
    rendered = render_markdown(report)
    assert "mock-embedding" in rendered
    assert "是否 Mock | True" in rendered or "是否 Mock | true" in rendered.lower()
    assert "64" in rendered
    assert "4C8G" in rendered
    assert "24.0 GiB" in rendered
    assert "不得写成" in report["disposition"] or "不能作为上线验收证据" in rendered
    json_path, markdown_path = write_report(report, tmp_path, "perf-five-metrics-test")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["health"]["embedding"]["provider"] == "mock"
    assert "清单机" in markdown_path.read_text(encoding="utf-8")


def test_extract_health_identity_keeps_mock_flag() -> None:
    identity = extract_health_identity(
        {
            "status": "ok",
            "env": "development",
            "checks": {
                "embedding": {
                    "mode": "mock",
                    "provider": "mock",
                    "model": "mock-embedding",
                    "dim": 64,
                    "is_mock": True,
                    "connectivity_verified": False,
                    "quality_evidence": False,
                },
                "vector_store": {"status": "ok", "backend": "local", "runtime": ""},
                "llm": {"mode": "mock"},
            },
        }
    )
    assert identity["embedding"]["is_mock"] is True
    assert identity["embedding"]["dim"] == 64




async def _stub_conversation_app(scope, receive, send):
    """立即完成的流式对话 ASGI 应用，用于验证 50 路并发测量本身。"""
    if scope["type"] != "http":
        return
    path = scope.get("path", "")
    headers = [(b"content-type", b"text/event-stream"), (b"x-run-id", b"stub-run")]
    if path.endswith("/chat/stream") and scope["method"] == "POST":
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send(
            {
                "type": "http.response.body",
                "body": b"event: conversation\ndata: stub-1\n\nevent: done\ndata: ok\n\n",
            }
        )
        return
    if "/runs/" in path and path.endswith("/cancel") and scope["method"] == "POST":
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"{}"})
        return
    await send({"type": "http.response.start", "status": 404, "headers": []})
    await send({"type": "http.response.body", "body": b""})


class _AppClient:
    def __init__(self, app) -> None:
        self.app = app


def test_concurrency_harness_can_start_fifty_stub_conversations() -> None:
    machine = {"matches_inventory": False, "app_process_count": 1}
    health = {
        "embedding": {"is_mock": True, "provider": "mock", "model": "mock-embedding", "dim": 64},
        "vector_store": {"backend": "local"},
        "llm": {"mode": "mock"},
    }
    metric = finalize_metric(
        measure_concurrency(
            _AppClient(_stub_conversation_app),
            conversations=CONCURRENT_CONVERSATIONS_TARGET,
            message="hello",
        ),
        health,
        machine,
    )
    assert metric.value == CONCURRENT_CONVERSATIONS_TARGET
    assert metric.meets_target is True
    assert metric.official_evidence is False
    assert metric.samples == CONCURRENT_CONVERSATIONS_TARGET


@pytest.fixture()
def perf_client(persist_identity, monkeypatch):
    user = User(
        id="perf-test-user",
        tenant_id="perf-test-tenant",
        username="perf-tester",
        hashed_password="",
        role=Role.MEMBER.value,
        token_version=0,
        is_active=True,
    )
    persist_identity(user)
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "local")
    monkeypatch.setattr(settings, "RAG_ENABLED", True)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT", False)
    app.dependency_overrides[get_current_user] = lambda: user
    set_llm_provider_override(MockLLMProvider())
    set_embedding_override(MockEmbeddingProvider(dim=64))
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    set_llm_provider_override(None)
    set_embedding_override(None)


def test_isolated_benchmark_measures_five_metrics(perf_client: TestClient, tmp_path: Path) -> None:
    health = snapshot_health(perf_client)
    assert health["embedding"]["is_mock"] is True
    machine = collect_machine_specs()
    report = run_benchmark(
        perf_client,
        health=health,
        machine=machine,
        api_samples=8,
        first_token_samples=3,
        retrieval_samples=8,
        conversations=0,
    )
    concurrency = finalize_metric(
        measure_concurrency(
            _AppClient(_stub_conversation_app),
            conversations=CONCURRENT_CONVERSATIONS_TARGET,
            message="hello",
        ),
        health,
        machine,
    )
    report["metrics"].insert(3, concurrency.as_dict())
    report["all_measured"] = all(item["value"] is not None for item in report["metrics"])
    keys = {item["key"] for item in report["metrics"]}
    assert keys == {
        "api_response_p95",
        "first_token_p95",
        "retrieval_p95",
        "concurrent_conversations",
        "document_upload",
    }
    assert report["all_measured"] is True
    assert report["release_evidence_valid"] is False
    by_key = {item["key"]: item for item in report["metrics"]}
    assert by_key["retrieval_p95"]["meets_target"] in {True, False}
    assert by_key["retrieval_p95"]["official_evidence"] is False
    assert any("Mock" in item for item in by_key["retrieval_p95"]["official_reasons"])
    assert by_key["document_upload"]["official_evidence"] is False
    assert by_key["document_upload"]["details"]["file_bytes"] == PDF_SIZE_BYTES
    assert by_key["first_token_p95"]["official_evidence"] is False
    assert by_key["concurrent_conversations"]["official_evidence"] is False
    assert by_key["api_response_p95"]["target"] == API_P95_MS_TARGET
    assert by_key["first_token_p95"]["target"] == FIRST_TOKEN_P95_MS_TARGET
    assert by_key["retrieval_p95"]["target"] == RETRIEVAL_P95_MS_TARGET
    assert by_key["document_upload"]["target"] == PDF_UPLOAD_SECONDS_TARGET
    assert "Asia/Shanghai" in report["started_at"]
    assert report["health"]["embedding"]["model"]
    assert report["machine"]["app_process_count"] == 1
    write_report(report, tmp_path, "isolated-run")
    rendered = (tmp_path / "isolated-run.md").read_text(encoding="utf-8")
    assert "不得写成" in report["disposition"] or "不能作为上线验收证据" in report["disposition"]
    assert "mock" in rendered.lower()
