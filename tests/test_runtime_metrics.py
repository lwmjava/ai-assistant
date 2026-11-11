"""运行时指标端点与计数、分位口径。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.runtime_metrics import (
    WINDOW_SECONDS,
    percentile,
    record_http_request,
    render_prometheus,
    reset_for_tests,
    snapshot_metrics,
)
from app.main import app

# 10 组合成事件：7 次成功、1 次 4xx、2 次 5xx；其中 8 次非推理、2 次推理。
_SYNTHETIC = (
    ("/api/v1/admin/system/status", 200, 0.010),
    ("/api/v1/admin/system/status", 200, 0.020),
    ("/api/v1/admin/users", 200, 0.030),
    ("/api/v1/auth/login", 200, 0.040),
    ("/api/v1/rag/documents", 200, 0.050),
    ("/api/v1/skills", 200, 0.080),
    ("/api/v1/memories", 200, 0.120),
    ("/api/v1/auth/login", 401, 0.015),
    ("/api/v1/chat", 502, 1.800),
    ("/api/v1/chat/stream", 500, 2.400),
)


def setup_function() -> None:
    reset_for_tests(started_at=1_000.0)


def test_metric_percentile_uses_nearest_rank() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    assert percentile(values, 50) == 20.0
    assert percentile(values, 95) == 40.0
    assert percentile([], 50) is None


def test_metric_snapshot_is_recomputable_from_ten_synthetic_events() -> None:
    now = 2_000.0
    for path, status, duration in _SYNTHETIC:
        record_http_request(path, status, duration, now=now)

    snap = snapshot_metrics(now=now)
    assert snap["alive"] is True
    assert snap["uptime_seconds"] == 1000.0
    assert snap["window_seconds"] == WINDOW_SECONDS
    requests = snap["requests"]
    assert requests["total"] == 10
    assert requests["success"] == 7
    assert requests["client_error"] == 1
    assert requests["server_error"] == 2
    assert requests["error_rate"] == 0.2

    non_inference = [row[2] for row in _SYNTHETIC if not row[0].startswith("/api/v1/chat")]
    inference = [row[2] for row in _SYNTHETIC if row[0].startswith("/api/v1/chat")]
    assert snap["latency"]["non_inference"]["sample_count"] == 8
    assert snap["latency"]["non_inference"]["p50_seconds"] == percentile(non_inference, 50)
    assert snap["latency"]["non_inference"]["p95_seconds"] == percentile(non_inference, 95)
    assert snap["latency"]["non_inference"]["p99_seconds"] == percentile(non_inference, 99)
    assert snap["latency"]["inference"]["sample_count"] == 2
    assert snap["latency"]["inference"]["p95_seconds"] == percentile(inference, 95)


def test_metric_unmeasured_values_are_null_not_zero() -> None:
    snap = snapshot_metrics(now=2_000.0)
    assert snap["requests"]["total"] == 0
    assert snap["requests"]["error_rate"] is None
    assert snap["latency"]["non_inference"]["sample_count"] == 0
    assert snap["latency"]["non_inference"]["p95_seconds"] is None
    text = render_prometheus(snap)
    assert "process_up 1" in text
    assert "http_request_error_ratio" not in text
    assert "http_request_duration_seconds" not in text


def test_metric_text_has_no_high_cardinality_labels() -> None:
    now = 2_000.0
    record_http_request("/api/v1/admin/users/alice", 200, 0.02, now=now)
    text = render_prometheus(snapshot_metrics(now=now))
    forbidden = ("alice", "tenant", "user_id", "tenant_id", "/api/v1/admin/users/alice")
    for token in forbidden:
        assert token not in text
    assert 'http_requests{outcome="success"} 1' in text


def test_metric_window_drops_expired_events() -> None:
    record_http_request("/api/v1/skills", 500, 0.05, now=1_000.0)
    snap = snapshot_metrics(now=1_000.0 + WINDOW_SECONDS + 1)
    assert snap["requests"]["total"] == 0
    assert snap["requests"]["error_rate"] is None


def test_metric_endpoint_returns_key_fields() -> None:
    for path, status, duration in _SYNTHETIC:
        record_http_request(path, status, duration)

    client = TestClient(app)
    json_resp = client.get("/api/v1/metrics", params={"format": "json"})
    assert json_resp.status_code == 200
    body = json_resp.json()
    assert body["alive"] is True
    assert "uptime_seconds" in body
    assert body["requests"]["total"] == 10
    assert body["requests"]["error_rate"] == 0.2
    assert body["latency"]["non_inference"]["p95_seconds"] == percentile(
        [0.010, 0.020, 0.030, 0.040, 0.050, 0.080, 0.120, 0.015], 95
    )

    text_resp = client.get("/api/v1/metrics")
    assert text_resp.status_code == 200
    assert "text/plain" in text_resp.headers["content-type"]
    text = text_resp.text
    assert "process_up 1" in text
    assert "http_request_error_ratio 0.2" in text
    assert 'http_request_duration_seconds{quantile="0.95",scope="non_inference"}' in text


def test_metric_endpoint_and_health_are_not_counted() -> None:
    client = TestClient(app)
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/metrics?format=json").status_code == 200
    body = client.get("/api/v1/metrics", params={"format": "json"}).json()
    assert body["requests"]["total"] == 0
    assert body["requests"]["error_rate"] is None


def test_metric_middleware_records_api_requests() -> None:
    client = TestClient(app)
    missing = client.get("/api/v1/definitely-missing")
    assert missing.status_code == 404
    body = client.get("/api/v1/metrics", params={"format": "json"}).json()
    assert body["requests"]["total"] == 1
    assert body["requests"]["client_error"] == 1
    assert body["requests"]["error_rate"] == 0.0
    assert body["latency"]["non_inference"]["sample_count"] == 1
    assert body["latency"]["non_inference"]["p95_seconds"] is not None
