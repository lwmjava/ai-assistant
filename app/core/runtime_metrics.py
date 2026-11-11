"""本进程运行时指标：存活、HTTP 错误率与延迟。

单实例、进程内统计；不汇总多副本，也不向外发送告警。
分位数采用最近秩（nearest-rank）；没有样本时返回 None，不用 0 表示未测到。
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

WINDOW_SECONDS = 300
_MAX_EVENTS = 10_000
_METRICS_PATH = "/api/v1/metrics"
_HEALTH_PATH = "/api/v1/health"

# 对话生成类路径的耗时含模型推理，不纳入 API P95（不含 LLM）口径。
_CHAT_STREAM = "/api/v1/chat/stream"
_CHAT_EXACT = "/api/v1/chat"
_CHAT_CONVERSATIONS = "/api/v1/chat/conversations/"

_lock = threading.Lock()
_started_at = time.time()


@dataclass(frozen=True, slots=True)
class _Event:
    at: float
    status_code: int
    duration_seconds: float
    scope: str


_events: deque[_Event] = deque()


def _outcome(status_code: int) -> str:
    if status_code >= 500:
        return "server_error"
    if status_code >= 400:
        return "client_error"
    return "success"


def is_probe_path(path: str) -> bool:
    """探活与指标抓取本身不计入错误率和延迟。"""
    return path == _METRICS_PATH or path == _HEALTH_PATH


def request_scope(path: str) -> str:
    """将路径归为 non_inference 或 inference，不把路径本身当作标签。"""
    if path == _CHAT_EXACT or path == _CHAT_STREAM:
        return "inference"
    if path.startswith(_CHAT_CONVERSATIONS) and path.endswith("/regenerate"):
        return "inference"
    return "non_inference"


def percentile(values: Iterable[float], p: float) -> float | None:
    """最近秩分位数；p 为 0–100，空输入返回 None。"""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = math.ceil(p / 100.0 * len(ordered))
    index = min(max(rank, 1), len(ordered)) - 1
    return ordered[index]


def record_http_request(
    path: str,
    status_code: int,
    duration_seconds: float,
    *,
    now: float | None = None,
) -> None:
    """记录一次 API 请求。探活与指标抓取会被忽略。"""
    if not path.startswith("/api/") or is_probe_path(path):
        return
    moment = time.time() if now is None else now
    event = _Event(
        at=moment,
        status_code=int(status_code),
        duration_seconds=max(0.0, float(duration_seconds)),
        scope=request_scope(path),
    )
    with _lock:
        _events.append(event)
        _trim(moment)


def _trim(now: float) -> None:
    cutoff = now - WINDOW_SECONDS
    while _events and (_events[0].at < cutoff or len(_events) > _MAX_EVENTS):
        _events.popleft()


def _latency_block(samples: list[float]) -> dict:
    return {
        "sample_count": len(samples),
        "p50_seconds": percentile(samples, 50),
        "p95_seconds": percentile(samples, 95),
        "p99_seconds": percentile(samples, 99),
    }


def _snapshot_locked(now: float) -> dict:
    _trim(now)
    counts = {"success": 0, "client_error": 0, "server_error": 0}
    latencies: dict[str, list[float]] = {"non_inference": [], "inference": []}
    for event in _events:
        counts[_outcome(event.status_code)] += 1
        latencies[event.scope].append(event.duration_seconds)
    total = counts["success"] + counts["client_error"] + counts["server_error"]
    error_rate = (counts["server_error"] / total) if total else None
    return {
        "alive": True,
        "uptime_seconds": round(max(0.0, now - _started_at), 3),
        "window_seconds": WINDOW_SECONDS,
        "requests": {
            "total": total,
            "success": counts["success"],
            "client_error": counts["client_error"],
            "server_error": counts["server_error"],
            "error_rate": error_rate,
        },
        "latency": {scope: _latency_block(samples) for scope, samples in latencies.items()},
    }


def snapshot_metrics(*, now: float | None = None) -> dict:
    """返回当前窗口内的基础指标快照。"""
    moment = time.time() if now is None else now
    with _lock:
        return _snapshot_locked(moment)


def _format_value(value: float) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    rendered = f"{value:.6f}".rstrip("0").rstrip(".")
    return rendered if rendered else "0"


def render_prometheus(snapshot: dict | None = None) -> str:
    """将快照渲染为 Prometheus 文本；未测到的比率和分位数整行省略。"""
    snap = snapshot if snapshot is not None else snapshot_metrics()
    requests = snap["requests"]
    lines = [
        "# HELP process_up 1 if this process is serving requests.",
        "# TYPE process_up gauge",
        f"process_up {1 if snap['alive'] else 0}",
        "# HELP process_uptime_seconds Seconds since this process started.",
        "# TYPE process_uptime_seconds gauge",
        f"process_uptime_seconds {_format_value(snap['uptime_seconds'])}",
        "# HELP http_requests HTTP request counts in the current window by outcome.",
        "# TYPE http_requests gauge",
        f'http_requests{{outcome="success"}} {requests["success"]}',
        f'http_requests{{outcome="client_error"}} {requests["client_error"]}',
        f'http_requests{{outcome="server_error"}} {requests["server_error"]}',
    ]
    if requests["error_rate"] is not None:
        lines.extend(
            [
                "# HELP http_request_error_ratio Server-error ratio in the current window.",
                "# TYPE http_request_error_ratio gauge",
                f"http_request_error_ratio {_format_value(requests['error_rate'])}",
            ]
        )
    latency_header = False
    for scope, values in snap["latency"].items():
        for quantile, key in (("0.5", "p50_seconds"), ("0.95", "p95_seconds"), ("0.99", "p99_seconds")):
            value = values[key]
            if value is None:
                continue
            if not latency_header:
                lines.extend(
                    [
                        "# HELP http_request_duration_seconds Request latency quantiles in the current window.",
                        "# TYPE http_request_duration_seconds gauge",
                    ]
                )
                latency_header = True
            lines.append(
                f'http_request_duration_seconds{{quantile="{quantile}",scope="{scope}"}} {_format_value(value)}'
            )
    lines.append("")
    return "\n".join(lines)


def reset_for_tests(*, started_at: float | None = None) -> None:
    """测试隔离：清空窗口内事件。"""
    global _started_at
    with _lock:
        _events.clear()
        if started_at is not None:
            _started_at = started_at
