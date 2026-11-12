"""五项性能指标的测量、门槛判定与报告生成。

默认可在进程内隔离环境跑完；检索和含向量化的文档处理若使用 Mock Embedding，
只记录实测值，不能写成上线达标。付费模型调用默认走 Mock。
"""

from __future__ import annotations

import json
import math
import os
import platform
import re
import subprocess
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

API_P95_MS_TARGET = 500.0
FIRST_TOKEN_P95_MS_TARGET = 2000.0
RETRIEVAL_P95_MS_TARGET = 300.0
CONCURRENT_CONVERSATIONS_TARGET = 50
PDF_UPLOAD_SECONDS_TARGET = 30.0
INVENTORY_CPU_CORES = 4
INVENTORY_RAM_GIB = 8
PDF_SIZE_BYTES = 10 * 1024 * 1024

_EXTRACTABLE_PDF_TEXT = (
    "Northwind billing guide. The standard plan price is 199 per month. "
    "The night plan price is 59 per month. Support hours are 9 to 18 on weekdays. "
    "Refund requests must be submitted within 7 days of the charge."
)


def percentile(values: Iterable[float], p: float) -> float:
    """线性插值百分位。空序列会报错。"""
    ordered = sorted(float(item) for item in values)
    if not ordered:
        raise ValueError("百分位计算需要至少一个样本")
    if not 0 <= p <= 100:
        raise ValueError("百分位必须在 0 到 100 之间")
    if len(ordered) == 1:
        return ordered[0]
    rank = (p / 100.0) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def p95(values: Iterable[float]) -> float:
    return percentile(values, 95)


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


def format_shanghai(stamp: datetime | None = None) -> str:
    current = stamp or now_shanghai()
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI)
    else:
        current = current.astimezone(SHANGHAI)
    return current.strftime("%Y-%m-%d %H:%M:%S") + " (Asia/Shanghai)"


def _physical_memory_bytes() -> int | None:
    if os.name == "nt":
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
        return None
    try:
        return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return None


def _physical_cpu_cores() -> int | None:
    if os.name == "nt":
        commands = [
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum",
            ],
            ["wmic", "cpu", "get", "NumberOfCores", "/value"],
        ]
        for command in commands:
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            digits = re.findall(r"\d+", completed.stdout or "")
            if digits:
                return int(digits[-1])
        return None
    try:
        return os.cpu_count()
    except NotImplementedError:
        return None


def collect_machine_specs() -> dict[str, Any]:
    """采集当前机器规格，并对照清单机 4C8G。"""
    logical = os.cpu_count()
    physical = _physical_cpu_cores()
    memory_bytes = _physical_memory_bytes()
    ram_gib = None if memory_bytes is None else memory_bytes / (1024 ** 3)
    matches = (
        physical == INVENTORY_CPU_CORES
        and ram_gib is not None
        and abs(ram_gib - INVENTORY_RAM_GIB) <= 1.0
    )
    return {
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "processor": platform.processor() or platform.machine(),
        "logical_processors": logical,
        "physical_cores": physical,
        "ram_bytes": memory_bytes,
        "ram_gib": None if ram_gib is None else round(ram_gib, 2),
        "python": platform.python_version(),
        "app_process_count": 1,
        "inventory_machine": {
            "cpu_cores": INVENTORY_CPU_CORES,
            "ram_gib": INVENTORY_RAM_GIB,
            "app_process_count": 1,
        },
        "matches_inventory": bool(matches),
    }


def build_sized_pdf(*, target_bytes: int = PDF_SIZE_BYTES, body: str = _EXTRACTABLE_PDF_TEXT) -> bytes:
    """构造指定体积的可解析 PDF；填充放在未引用对象中，不进入文本层。"""
    if target_bytes < 1024:
        raise ValueError("PDF 目标体积过小")
    safe_body = body.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content_stream = f"BT /F1 12 Tf 72 720 Td ({safe_body}) Tj ET\n".encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(content_stream) + content_stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    # 先按当前对象估算，再把未引用填充对象补到目标体积。
    header = b"%PDF-1.4\n"
    placeholder = b"<< /Length 0 >>\nstream\n\nendstream"
    objects.append(placeholder)

    def assemble(payloads: list[bytes]) -> bytes:
        parts = [header]
        offsets = [0]
        cursor = len(header)
        for payload in payloads:
            encoded = f"{len(offsets)} 0 obj\n".encode("ascii") + payload + b"\nendobj\n"
            offsets.append(cursor)
            parts.append(encoded)
            cursor += len(encoded)
        xref_start = cursor
        xref = [b"xref\n0 %d\n" % (len(payloads) + 1), b"0000000000 65535 f \n"]
        for offset in offsets[1:]:
            xref.append(b"%010d 00000 n \n" % offset)
        xref_block = b"".join(xref)
        trailer = (
            b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(payloads) + 1, xref_start)
        )
        return b"".join(parts) + xref_block + trailer

    skeleton = assemble(objects)
    pad_needed = target_bytes - len(skeleton)
    if pad_needed < 0:
        raise ValueError("PDF 正文已超过目标体积")
    padding = b"X" * pad_needed
    objects[-1] = b"<< /Length %d >>\nstream\n" % pad_needed + padding + b"\nendstream"
    result = assemble(objects)
    # 末尾微调到精确目标体积。
    if len(result) < target_bytes:
        result += b" " * (target_bytes - len(result))
    elif len(result) > target_bytes:
        overflow = len(result) - target_bytes
        if overflow <= pad_needed:
            padding = b"X" * (pad_needed - overflow)
            objects[-1] = b"<< /Length %d >>\nstream\n" % len(padding) + padding + b"\nendstream"
            result = assemble(objects)
            if len(result) < target_bytes:
                result += b" " * (target_bytes - len(result))
    return result[:target_bytes] if len(result) > target_bytes else result


def parse_sse_frames(buffer: str) -> list[tuple[str, str]]:
    """解析 SSE 帧，返回 (event, data) 列表。"""
    normalized = buffer.replace("\r\n", "\n")
    frames: list[tuple[str, str]] = []
    for block in normalized.split("\n\n"):
        if not block.strip():
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        frames.append((event_name, "\n".join(data_lines)))
    return frames


def first_token_from_frames(frames: Iterable[tuple[str, str]]) -> bool:
    for name, data in frames:
        if name == "token" and data.strip():
            return True
    return False


def extract_health_identity(health: Mapping[str, Any]) -> dict[str, Any]:
    checks = health.get("checks") or {}
    embedding = dict(checks.get("embedding") or {})
    vector = dict(checks.get("vector_store") or {})
    llm = dict(checks.get("llm") or {})
    return {
        "status": health.get("status"),
        "env": health.get("env"),
        "embedding": {
            "mode": embedding.get("mode"),
            "provider": embedding.get("provider"),
            "model": embedding.get("model"),
            "dim": embedding.get("dim"),
            "is_mock": bool(embedding.get("is_mock")),
            "connectivity_verified": embedding.get("connectivity_verified"),
            "quality_evidence": embedding.get("quality_evidence"),
        },
        "vector_store": {
            "status": vector.get("status"),
            "backend": vector.get("backend"),
            "runtime": vector.get("runtime"),
        },
        "llm": {"mode": llm.get("mode")},
    }


@dataclass
class MetricResult:
    key: str
    title: str
    value: float | None
    unit: str
    target: float
    comparison: str
    samples: int = 0
    meets_target: bool = False
    official_evidence: bool = False
    official_reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def _compare(value: float, target: float, comparison: str) -> bool:
    if comparison == "<=":
        return value <= target
    if comparison == ">=":
        return value >= target
    raise ValueError(f"未知比较符: {comparison}")


def official_reasons(
    *,
    key: str,
    health: Mapping[str, Any],
    machine: Mapping[str, Any],
) -> list[str]:
    """判断该指标的实测值能否作为上线证据。"""
    reasons: list[str] = []
    if not machine.get("matches_inventory"):
        reasons.append("当前机器不是清单机 4C8G")
    if int(machine.get("app_process_count") or 0) != 1:
        reasons.append("不是单应用进程测量")
    embedding = (health.get("embedding") or {})
    llm_mode = (health.get("llm") or {}).get("mode")
    backend = (health.get("vector_store") or {}).get("backend")
    if key in {"retrieval_p95", "document_upload"} and embedding.get("is_mock"):
        reasons.append("Embedding 为 Mock，不得写成上线性能数字")
    if key == "retrieval_p95" and backend != "milvus":
        reasons.append(f"向量库为 {backend or 'unknown'}，检索指标要求 Milvus")
    if key in {"first_token_p95", "concurrent_conversations"} and llm_mode != "real":
        reasons.append(f"LLM 模式为 {llm_mode or 'unknown'}，不得写成上线对话性能数字")
    return reasons


def finalize_metric(metric: MetricResult, health: Mapping[str, Any], machine: Mapping[str, Any]) -> MetricResult:
    reasons = official_reasons(key=metric.key, health=health, machine=machine)
    if metric.value is None:
        metric.meets_target = False
        metric.official_evidence = False
        metric.official_reasons = reasons or ["没有实测值"]
        if metric.error:
            metric.official_reasons = list(dict.fromkeys([*metric.official_reasons, metric.error]))
        return metric
    metric.meets_target = _compare(metric.value, metric.target, metric.comparison)
    metric.official_reasons = reasons
    metric.official_evidence = metric.meets_target and not reasons
    return metric


def snapshot_health(client: Any) -> dict[str, Any]:
    response = client.get("/api/v1/health")
    payload = response.json() if hasattr(response, "json") else json.loads(response.content)
    identity = extract_health_identity(payload)
    identity["http_status"] = getattr(response, "status_code", None)
    return identity


def measure_api_latency(client: Any, *, samples: int, paths: list[str] | None = None) -> MetricResult:
    """测量不含 LLM 的 API 响应时间。"""
    targets = paths or ["/api/v1/health", "/api/v1/chat/conversations"]
    timings: list[float] = []
    statuses: list[int] = []
    try:
        for index in range(samples):
            path = targets[index % len(targets)]
            started = time.perf_counter()
            response = client.get(path)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            statuses.append(int(response.status_code))
            if response.status_code >= 400:
                raise RuntimeError(f"{path} 返回 {response.status_code}")
            timings.append(elapsed_ms)
        metric = MetricResult(
            key="api_response_p95",
            title="API 响应时间 (P95，不含 LLM)",
            value=p95(timings),
            unit="ms",
            target=API_P95_MS_TARGET,
            comparison="<=",
            samples=len(timings),
            details={"paths": targets, "status_codes": statuses, "max_ms": max(timings), "min_ms": min(timings)},
        )
    except Exception as exc:  # noqa: BLE001
        metric = MetricResult(
            key="api_response_p95",
            title="API 响应时间 (P95，不含 LLM)",
            value=None,
            unit="ms",
            target=API_P95_MS_TARGET,
            comparison="<=",
            samples=len(timings),
            error=type(exc).__name__,
            details={"paths": targets},
        )
    return metric


def _iter_sse_text(response: Any) -> str:
    if hasattr(response, "iter_text"):
        return "".join(response.iter_text())
    if hasattr(response, "text"):
        return response.text
    return response.content.decode("utf-8") if hasattr(response, "content") else str(response)


def _format_exc(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        inner = ", ".join(f"{type(item).__name__}: {item}" for item in exc.exceptions)
        return f"{type(exc).__name__}({inner})"
    return f"{type(exc).__name__}: {exc}"


def _reset_sse_loop_state() -> None:
    """清掉上一次流式响应留在进程里的事件循环绑定。"""
    try:
        from sse_starlette.sse import AppStatus
    except Exception:  # noqa: BLE001
        return
    AppStatus.should_exit_event = None
    AppStatus.should_exit = False


def measure_first_token(client: Any, *, samples: int, message: str) -> MetricResult:
    timings: list[float] = []
    try:
        for _ in range(samples):
            last_error: BaseException | None = None
            first_ms: float | None = None
            for _attempt in range(3):
                started = time.perf_counter()
                first_ms = None
                try:
                    _reset_sse_loop_state()
                    with client.stream("POST", "/api/v1/chat/stream", json={"message": message}) as response:
                        if response.status_code >= 400:
                            raise RuntimeError(f"流式对话返回 {response.status_code}")
                        buffer = ""
                        for chunk in response.iter_text():
                            buffer += chunk
                            if first_ms is None and first_token_from_frames(parse_sse_frames(buffer)):
                                first_ms = (time.perf_counter() - started) * 1000.0
                    if first_ms is None:
                        raise RuntimeError("流式响应中没有 token 事件")
                    break
                except BaseException as exc:  # noqa: BLE001
                    last_error = exc
                    time.sleep(0.05)
            if first_ms is None:
                raise last_error or RuntimeError("流式响应中没有 token 事件")
            timings.append(first_ms)
        metric = MetricResult(
            key="first_token_p95",
            title="首 Token 延迟 (P95)",
            value=p95(timings),
            unit="ms",
            target=FIRST_TOKEN_P95_MS_TARGET,
            comparison="<=",
            samples=len(timings),
            details={"max_ms": max(timings), "min_ms": min(timings)},
        )
    except Exception as exc:  # noqa: BLE001
        metric = MetricResult(
            key="first_token_p95",
            title="首 Token 延迟 (P95)",
            value=None,
            unit="ms",
            target=FIRST_TOKEN_P95_MS_TARGET,
            comparison="<=",
            samples=len(timings),
            error=_format_exc(exc),
        )
    return metric


def measure_retrieval(client: Any, *, samples: int, query: str) -> MetricResult:
    timings: list[float] = []
    hit_counts: list[int] = []
    try:
        for _ in range(samples):
            started = time.perf_counter()
            response = client.post("/api/v1/rag/search", json={"query": query, "top_k": 5})
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if response.status_code >= 400:
                raise RuntimeError(f"检索返回 {response.status_code}")
            payload = response.json()
            hit_counts.append(len(payload) if isinstance(payload, list) else 0)
            timings.append(elapsed_ms)
        metric = MetricResult(
            key="retrieval_p95",
            title="知识库检索延迟 (P95)",
            value=p95(timings),
            unit="ms",
            target=RETRIEVAL_P95_MS_TARGET,
            comparison="<=",
            samples=len(timings),
            details={"query": query, "hit_counts": hit_counts, "max_ms": max(timings), "min_ms": min(timings)},
        )
    except Exception as exc:  # noqa: BLE001
        metric = MetricResult(
            key="retrieval_p95",
            title="知识库检索延迟 (P95)",
            value=None,
            unit="ms",
            target=RETRIEVAL_P95_MS_TARGET,
            comparison="<=",
            samples=len(timings),
            error=type(exc).__name__,
        )
    return metric


def _run_coroutine(coro):
    """在独立事件循环里跑协程，避免占用 TestClient 所在线程。"""
    import asyncio
    import threading

    box: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            error["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join(timeout=60)
    if thread.is_alive():
        raise TimeoutError("并发对话测量超时")
    if "error" in error:
        raise error["error"]
    if "result" not in box:
        raise RuntimeError("并发对话测量未能返回")
    return box["result"]


async def _concurrent_streams(app: Any, *, conversations: int, message: str) -> tuple[int, list[float], list[int]]:
    """同时发起多路流式对话，等到会话已建立后取消，避免把完整管线跑完拖死隔离库。"""
    import asyncio

    import httpx

    timeout = httpx.Timeout(20.0, connect=10.0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://perf-isolated", timeout=timeout) as client:
        async def _one() -> tuple[bool, float, int]:
            started = time.perf_counter()
            run_id = None
            status = 0
            started_ok = False
            try:
                async with client.stream("POST", "/api/v1/chat/stream", json={"message": message}) as response:
                    status = int(response.status_code)
                    run_id = response.headers.get("x-run-id") or response.headers.get("X-Run-ID")
                    if status >= 400:
                        return False, time.perf_counter() - started, status
                    parts: list[str] = []
                    async for chunk in response.aiter_text():
                        parts.append(chunk)
                        frames = parse_sse_frames("".join(parts))
                        if any(name in {"conversation", "message", "token"} and data.strip() for name, data in frames):
                            started_ok = True
                            break
            finally:
                if run_id:
                    try:
                        await client.post(f"/api/v1/runs/{run_id}/cancel")
                    except Exception:  # noqa: BLE001
                        pass
            return started_ok, time.perf_counter() - started, status

        tasks = [asyncio.create_task(_one()) for _ in range(conversations)]
        done, pending = await asyncio.wait(tasks, timeout=45)
        for task in pending:
            task.cancel()
        results: list[Any] = []
        for task in done:
            try:
                results.append(task.result())
            except Exception as exc:  # noqa: BLE001
                results.append(exc)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            results.extend(TimeoutError("in-flight conversation did not finish") for _ in pending)

    successes = 0
    timings: list[float] = []
    statuses: list[int] = []
    errors: list[str] = []
    for item in results:
        if isinstance(item, Exception):
            timings.append(0.0)
            statuses.append(0)
            errors.append(type(item).__name__)
            continue
        ok, elapsed, status = item
        timings.append(elapsed)
        statuses.append(status)
        if ok:
            successes += 1
    if errors and successes == 0:
        raise RuntimeError("并发对话全部失败: " + ",".join(sorted(set(errors))))
    return successes, timings, statuses


def measure_concurrency(client: Any, *, conversations: int, message: str) -> MetricResult:
    successes = 0
    timings: list[float] = []
    statuses: list[int] = []
    try:
        app = getattr(client, "app", None)
        if app is None:
            raise RuntimeError("当前客户端不能安全地并发发起对话")
        successes, timings, statuses = _run_coroutine(
            _concurrent_streams(app, conversations=conversations, message=message)
        )
        metric = MetricResult(
            key="concurrent_conversations",
            title="并发对话数",
            value=float(successes),
            unit="conversations",
            target=float(CONCURRENT_CONVERSATIONS_TARGET),
            comparison=">=",
            samples=conversations,
            details={
                "requested": conversations,
                "successes": successes,
                "failures": conversations - successes,
                "status_codes": statuses,
                "max_s": max(timings) if timings else None,
            },
        )
    except Exception as exc:  # noqa: BLE001
        metric = MetricResult(
            key="concurrent_conversations",
            title="并发对话数",
            value=None,
            unit="conversations",
            target=float(CONCURRENT_CONVERSATIONS_TARGET),
            comparison=">=",
            samples=conversations,
            error=type(exc).__name__,
        )
    return metric


def measure_pdf_upload(client: Any, *, pdf_bytes: bytes, filename: str = "perf-10mib.pdf") -> MetricResult:
    started = time.perf_counter()
    try:
        response = client.post(
            "/api/v1/rag/documents/upload",
            files={"file": (filename, pdf_bytes, "application/pdf")},
        )
        elapsed = time.perf_counter() - started
        if response.status_code >= 400:
            raise RuntimeError(f"上传返回 {response.status_code}: {getattr(response, 'text', '')[:200]}")
        payload = response.json()
        storage_path = payload.get("storage_path")
        metric = MetricResult(
            key="document_upload",
            title="文档上传处理（10MB PDF 到已向量化）",
            value=elapsed,
            unit="s",
            target=PDF_UPLOAD_SECONDS_TARGET,
            comparison="<=",
            samples=1,
            details={
                "file_bytes": len(pdf_bytes),
                "vectorization_status": payload.get("vectorization_status"),
                "chunk_count": payload.get("chunk_count"),
                "vectorized_chunk_count": payload.get("vectorized_chunk_count"),
                "document_id": payload.get("id"),
                "storage_path": storage_path,
                "ready": payload.get("vectorization_status") == "vectorized",
            },
        )
        if payload.get("vectorization_status") != "vectorized":
            metric.error = f"结束状态为 {payload.get('vectorization_status')}，未到已向量化"
            metric.value = elapsed
    except Exception as exc:  # noqa: BLE001
        metric = MetricResult(
            key="document_upload",
            title="文档上传处理（10MB PDF 到已向量化）",
            value=None,
            unit="s",
            target=PDF_UPLOAD_SECONDS_TARGET,
            comparison="<=",
            samples=1,
            error=type(exc).__name__,
            details={"file_bytes": len(pdf_bytes)},
        )
    return metric


def ensure_corpus(client: Any) -> dict[str, Any]:
    """写入一篇短文，供检索和对话使用。"""
    response = client.post(
        "/api/v1/rag/documents/ingest",
        json={
            "text": _EXTRACTABLE_PDF_TEXT,
            "title": "billing-guide",
            "source": "perf-benchmark.txt",
            "version_state": "published",
        },
    )
    if response.status_code >= 400:
        raise RuntimeError(f"语料摄取失败: {response.status_code}")
    return response.json()



def _cleanup_active_runs() -> None:
    """尽量结束残留运行，避免隔离测试在关闭应用时卡住。"""
    try:
        import asyncio

        from app.services.active_runs import active_runs
    except Exception:  # noqa: BLE001
        return
    shutdown = getattr(active_runs, "shutdown", None)
    if shutdown is None:
        return
    try:
        asyncio.run(shutdown())
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(shutdown())
            else:
                loop.run_until_complete(shutdown())
        except Exception:  # noqa: BLE001
            return
    except Exception:  # noqa: BLE001
        return


def run_benchmark(
    client: Any,
    *,
    health: Mapping[str, Any],
    machine: Mapping[str, Any],
    api_samples: int = 20,
    first_token_samples: int = 8,
    retrieval_samples: int = 20,
    conversations: int = CONCURRENT_CONVERSATIONS_TARGET,
    pdf_bytes: bytes | None = None,
) -> dict[str, Any]:
    """在给定 HTTP 客户端上跑完五项指标并做门槛判定。"""
    started = now_shanghai()
    corpus = ensure_corpus(client)
    pdf_payload = pdf_bytes if pdf_bytes is not None else build_sized_pdf()
    metrics = [
        finalize_metric(measure_api_latency(client, samples=api_samples), health, machine),
        finalize_metric(
            measure_first_token(client, samples=first_token_samples, message="标准套餐每月多少钱？"),
            health,
            machine,
        ),
        finalize_metric(
            measure_retrieval(client, samples=retrieval_samples, query="standard plan price"),
            health,
            machine,
        ),
        finalize_metric(measure_pdf_upload(client, pdf_bytes=pdf_payload), health, machine),
    ]
    if conversations > 0:
        metrics.insert(
            3,
            finalize_metric(
                measure_concurrency(client, conversations=conversations, message="hello"),
                health,
                machine,
            ),
        )
    _cleanup_active_runs()
    all_measured = all(item.value is not None for item in metrics)
    release_evidence_valid = all(item.official_evidence for item in metrics)
    finished = now_shanghai()
    return {
        "title": "五项性能指标隔离压测",
        "started_at": format_shanghai(started),
        "finished_at": format_shanghai(finished),
        "timezone": "Asia/Shanghai",
        "machine": dict(machine),
        "health": dict(health),
        "corpus": {"document_id": corpus.get("id"), "vectorization_status": corpus.get("vectorization_status")},
        "pdf": {
            "file_bytes": len(pdf_payload),
            "extractable_text_chars": len(_EXTRACTABLE_PDF_TEXT),
            "padding": "unreferenced_stream",
            "note": "文件体积为 10MiB；填充位于未引用对象，不进入文本层，不能代表全文密排 PDF 的向量化耗时。",
        },
        "metrics": [item.as_dict() for item in metrics],
        "all_measured": all_measured,
        "release_evidence_valid": release_evidence_valid,
        "constraints": {
            "isolated": True,
            "single_app_process": True,
            "paid_model_calls_mocked": (health.get("llm") or {}).get("mode") != "real",
            "mock_embedding_used": bool((health.get("embedding") or {}).get("is_mock")),
            "not_production_stack": True,
        },
        "disposition": build_disposition(metrics, machine, health, all_measured, release_evidence_valid),
    }


def build_disposition(
    metrics: list[MetricResult],
    machine: Mapping[str, Any],
    health: Mapping[str, Any],
    all_measured: bool,
    release_evidence_valid: bool,
) -> str:
    lines: list[str] = []
    if all_measured:
        lines.append("五项指标均已测得数值。")
    else:
        missing = [item.title for item in metrics if item.value is None]
        lines.append("以下指标没有测得数值：" + "、".join(missing) + "。")
    if release_evidence_valid:
        lines.append("本跑满足上线证据条件，且五项均达到目标。")
    else:
        lines.append("本跑不能作为上线验收证据。")
        if not machine.get("matches_inventory"):
            ram = machine.get("ram_gib")
            cores = machine.get("physical_cores")
            lines.append(
                f"实际机器为 {cores or '?'} 核 / {ram or '?'} GiB，不是清单机 4C8G。"
            )
        if (health.get("embedding") or {}).get("is_mock"):
            lines.append("检索与文档处理使用了 Mock Embedding，不得写成达标。")
        if (health.get("llm") or {}).get("mode") != "real":
            lines.append("首 Token 与并发对话使用了非真实 LLM，不得写成上线数字。")
        backend = (health.get("vector_store") or {}).get("backend")
        if backend != "milvus":
            lines.append(f"当前向量库为 {backend or 'unknown'}，不是 Milvus。")
        unmet = [item.title for item in metrics if item.value is not None and not item.meets_target]
        if unmet:
            lines.append("即使只看数值，也尚未达到目标：" + "、".join(unmet) + "。")
        lines.append(
            "要形成可关卡的上线证据，需要在清单机 4C8G、单应用进程、真实 Embedding、"
            "Milvus 以及真实 LLM 上重跑同一脚本，并且五项都达到目标。不得为达标改机器规格或压测场景。"
        )
    return " ".join(lines)


def render_markdown(report: Mapping[str, Any]) -> str:
    health = report["health"]
    embedding = health["embedding"]
    machine = report["machine"]
    rows = []
    for item in report["metrics"]:
        value = "—" if item["value"] is None else f"{item['value']:.4g} {item['unit']}"
        target = f"{item['comparison']} {item['target']:g} {item['unit']}"
        meets = "是" if item["meets_target"] else "否"
        official = "是" if item["official_evidence"] else "否"
        reason = "；".join(item.get("official_reasons") or []) or "—"
        error = item.get("error") or "—"
        rows.append(
            f"| {item['title']} | {value} | {target} | {item['samples']} | {meets} | {official} | {reason} | {error} |"
        )
    inventory = "是" if machine.get("matches_inventory") else "否"
    ram = machine.get("ram_gib")
    cores = machine.get("physical_cores")
    logical = machine.get("logical_processors")
    body = f"""# 五项性能指标压测报告

- 开始：{report["started_at"]}
- 结束：{report["finished_at"]}
- 时区：Asia/Shanghai
- 复现：`python scripts/run_perf_benchmark.py --output-dir docs/evaluations`

## 环境

| 项 | 值 |
|---|---|
| 主机 | {machine.get("hostname")} |
| 操作系统 | {machine.get("os")} |
| 处理器 | {machine.get("processor")} |
| 物理核 / 逻辑核 | {cores} / {logical} |
| 内存 | {ram} GiB |
| 应用进程数 | {machine.get("app_process_count")} |
| 是否清单机 4C8G | {inventory} |
| Python | {machine.get("python")} |
| 运行方式 | 进程内隔离，单应用进程；不对生产或共享在线栈施压 |

## /health 所见 Embedding 与依赖

| 项 | 值 |
|---|---|
| 服务状态 | {health.get("status")}（HTTP {health.get("http_status")}） |
| 环境 | {health.get("env")} |
| Embedding provider | {embedding.get("provider")} |
| Embedding 模型 | {embedding.get("model")} |
| Embedding 维度 | {embedding.get("dim")} |
| 是否 Mock | {embedding.get("is_mock")} |
| connectivity_verified | {embedding.get("connectivity_verified")} |
| quality_evidence | {embedding.get("quality_evidence")} |
| 向量库 | {health["vector_store"].get("backend")} / {health["vector_store"].get("runtime")} |
| LLM 模式 | {health["llm"].get("mode")} |

检索延迟只有在 Embedding 非 Mock、且向量库为 Milvus 时，才能作为上线数字。

## 指标

| 指标 | 实测 | 目标 | 样本 | 数值达标 | 可作为上线证据 | 不能作为证据的原因 | 错误 |
|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

## 文档样本

文件体积 {report["pdf"]["file_bytes"]} 字节，可提取正文 {report["pdf"]["extractable_text_chars"]} 字符。
{report["pdf"]["note"]}

## 处置结论

{report["disposition"]}

## 约束

- 隔离本地基准，不打生产或共享在线栈。
- 付费模型默认 Mock；Mock 结果只证明链路，不写成达标。
- 不做多副本或跨实例压测，也不做极限容量规划。
"""
    return body


def write_report(report: Mapping[str, Any], output_dir: Path, stem: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
