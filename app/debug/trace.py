"""调试与追踪系统 — Agent 管线执行 trace。

提供：
- ``AgentTrace``：一次管线执行的完整追踪记录
- ``TraceEvent``：单次追踪事件（阶段/LLM 调用/工具调用）
- ``TraceCollector``：全局 trace 收集器（可选持久化）

当前实现：内存级 trace，不持久化。可按需扩展：
- Trace 持久化到数据库（按 run_id 查询历史）
- Trace 可视化（前端时间线展示）
- 性能分析（各阶段耗时统计）
"""

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


def _trace_id() -> str:
    """生成短 trace ID。"""
    return uuid.uuid4().hex[:12]


_TRACE_REDACTED = "（追踪内容已省略）"

_STAGE_METRIC_NAMES = {
    "skill_match": "skill_match",
    "route": "route",
    "understand": "understand",
    "理解": "understand",
    "preflight": "preflight",
    "意图分流": "preflight",
    "plan": "plan",
    "规划": "plan",
    "retrieval": "retrieval",
    "检索": "retrieval",
    "tools": "tools",
    "行动": "tools",
    "行动（短路）": "tools",
    "reflect": "reflect",
    "反思": "reflect",
    "answer": "answer",
    "respond": "answer",
    "响应": "answer",
    "quality_gate": "quality_gate",
    "质量检查": "quality_gate",
    "revision": "revision",
    "质量门自纠错": "revision",
}
_INTENT_PURPOSES = frozenset({"preflight", "skill_match", "意图分流"})


def _metric_stage_name(name: str) -> str:
    return _STAGE_METRIC_NAMES.get(name, name)


def _purpose_for(purpose: str | None, stage: str | None) -> str:
    if purpose:
        return purpose
    if stage in _INTENT_PURPOSES:
        return "intent"
    return "chat"



def sanitize_trace_text(text: str | None) -> str:
    """清理会写入 Trace 的文本。清理失败时返回固定占位。"""
    if not text:
        return ""
    try:
        from app.agents.tools.sandbox.runtime import sanitize_host_paths
        from app.security.input_filter import InputFilter
        from app.security.log_sanitizer import LogSanitizer

        cleaned = InputFilter().filter(text).sanitized_text
        cleaned = LogSanitizer().sanitize(cleaned)
        return sanitize_host_paths(cleaned)
    except Exception:
        return _TRACE_REDACTED


@dataclass
class TraceEvent:
    """单次追踪事件。

    记录管线执行中的一个关键时刻：阶段开始/结束、LLM 调用、工具调用。
    """

    type: str  # stage_start / stage_end / llm_call / tool_call / quality_gate
    name: str  # 阶段名 / 工具名 / LLM 模型名
    timestamp: float = 0.0  # Unix 时间戳（秒）
    data: dict = field(default_factory=dict)  # 附加数据（prompt/response/args 等）

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "name": self.name,
            "timestamp": self.timestamp,
            "data": self.data,
        }


@dataclass
class AgentTrace:
    """一次管线执行的完整追踪记录。

    使用方式::

        trace = AgentTrace(debug_mode=True)
        trace.stage_start("理解")
        # ... 执行理解阶段 ...
        trace.stage_end("理解", duration_ms=150)
        trace.llm_call("deepseek-chat", prompt="...", response="...", latency_ms=200)
        trace.tool_call("web_search", args={"q": "test"}, result="...", latency_ms=500)
        summary = trace.summary()
    """

    run_id: str = field(default_factory=_trace_id)
    debug_mode: bool = False
    started_at: float = 0.0
    finished_at: float = 0.0
    events: list[TraceEvent] = field(default_factory=list)
    error: str | None = None
    eval_version: str | None = None
    first_token_ms: float | None = None
    first_model_token_ms: float | None = None
    _request_started_at: float = field(default=0.0, init=False, repr=False)
    _stage_started: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _stage_durations_ms: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _llm_calls_by_purpose: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _llm_call_count: int = field(default=0, init=False, repr=False)
    _tool_call_count: int = field(default=0, init=False, repr=False)
    _llm_latency_ms: float = field(default=0.0, init=False, repr=False)
    _tool_latency_ms: float = field(default=0.0, init=False, repr=False)

    @property
    def duration_ms(self) -> float:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at) * 1000
        return 0.0

    def _now(self) -> float:
        return time.monotonic()

    def start(self) -> None:
        """标记追踪开始。"""
        self.started_at = self._now()
        if not self._request_started_at:
            self._request_started_at = self.started_at

    def finish(self, error: str | None = None) -> None:
        """标记追踪结束。异常说明先脱敏再保存。"""
        self.finished_at = self._now()
        self.error = sanitize_trace_text(error) if error else error

    # ── 阶段事件 ──

    def stage_start(self, name: str, **extra) -> None:
        now = self._now()
        self._stage_started[name] = now
        if not self.debug_mode:
            return
        self.events.append(TraceEvent(
            type="stage_start",
            name=name,
            timestamp=now,
            data=extra,
        ))

    def stage_end(self, name: str, **extra) -> None:
        now = self._now()
        started = self._stage_started.pop(name, None)
        if started is not None:
            duration = max(0.0, (now - started) * 1000)
            key = _metric_stage_name(name)
            self._stage_durations_ms[key] = self._stage_durations_ms.get(key, 0.0) + duration
            extra = {"duration_ms": round(duration, 2), **extra}
        if not self.debug_mode:
            return
        self.events.append(TraceEvent(
            type="stage_end",
            name=name,
            timestamp=now,
            data=extra,
        ))

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        self.stage_start(name)
        try:
            yield
        finally:
            self.stage_end(name)

    def mark_first_token(self, *, model: bool = False) -> None:
        origin = self._request_started_at or self.started_at
        if not origin:
            origin = self._now()
            self._request_started_at = origin
        elapsed = max(0.0, (self._now() - origin) * 1000)
        if self.first_token_ms is None:
            self.first_token_ms = round(elapsed, 2)
        if model and self.first_model_token_ms is None:
            self.first_model_token_ms = round(elapsed, 2)

    # ── LLM 调用 ──

    def llm_call(
        self,
        model: str,
        *,
        prompt: str = "",
        response: str = "",
        latency_ms: float = 0.0,
        purpose: str | None = None,
        stage: str | None = None,
        **extra,
    ) -> None:
        self._llm_call_count += 1
        self._llm_latency_ms += round(latency_ms, 2)
        key = _purpose_for(purpose, stage)
        self._llm_calls_by_purpose[key] = self._llm_calls_by_purpose.get(key, 0) + 1
        if stage is not None:
            extra = {"stage": stage, **extra}
        if purpose is not None:
            extra = {"purpose": purpose, **extra}
        if not self.debug_mode:
            return
        data: dict = {
            "prompt_preview": sanitize_trace_text(prompt)[:500],
            "response_preview": sanitize_trace_text(response)[:500],
            "latency_ms": round(latency_ms, 2),
        }
        for key, value in extra.items():
            data[key] = sanitize_trace_text(value)[:500] if isinstance(value, str) else value
        self.events.append(TraceEvent(
            type="llm_call",
            name=model,
            timestamp=self._now(),
            data=data,
        ))

    # ── 工具调用 ──

    def tool_call(
        self,
        tool_name: str,
        *,
        args: dict | None = None,
        result: str = "",
        latency_ms: float = 0.0,
        status: str = "executed",
        round_no: int | None = None,
        counted: bool = True,
        **extra,
    ) -> None:
        """记录工具名、状态、轮次和耗时。不保存参数、参数指纹、结果或错误原文。"""
        del args, result, extra
        if counted:
            self._tool_call_count += 1
            self._tool_latency_ms += round(latency_ms, 2)
        if not self.debug_mode:
            return
        self.events.append(TraceEvent(
            type="tool_call",
            name=tool_name,
            timestamp=self._now(),
            data={
                "status": status,
                "round": round_no,
                "latency_ms": round(latency_ms, 2),
            },
        ))

    # ── 质量门 ──

    def quality_gate(self, score: float, threshold: float, **extra) -> None:
        if not self.debug_mode:
            return
        self.events.append(TraceEvent(
            type="quality_gate",
            name="quality_gate",
            timestamp=self._now(),
            data={
                "score": score,
                "threshold": threshold,
                "passed": score >= threshold,
                **extra,
            },
        ))

    # ── 摘要 ──

    def summary(self) -> dict:
        """生成追踪摘要（供 SSE 事件 / 日志输出）。"""
        stages = [e for e in self.events if e.type in ("stage_start", "stage_end")]
        quality_gates = [e for e in self.events if e.type == "quality_gate"]

        return {
            "run_id": self.run_id,
            "debug_mode": self.debug_mode,
            "duration_ms": round(self.duration_ms, 2),
            "stage_count": len(stages) // 2,  # start/end pairs
            "llm_call_count": self._llm_call_count,
            "tool_call_count": self._tool_call_count,
            "total_llm_latency_ms": round(self._llm_latency_ms, 2),
            "total_tool_latency_ms": round(self._tool_latency_ms, 2),
            "stage_durations_ms": {key: round(value, 2) for key, value in self._stage_durations_ms.items()},
            "llm_calls_by_purpose": dict(self._llm_calls_by_purpose),
            "first_token_ms": self.first_token_ms,
            "first_model_token_ms": self.first_model_token_ms,
            "eval_version": self.eval_version,
            "quality_gate_passed": all(
                e.data.get("passed", True) for e in quality_gates
            ),
            "error": self.error,
        }

    def to_dict(self) -> dict:
        """完整追踪序列化（供调试 API 返回）。"""
        return {
            "run_id": self.run_id,
            "debug_mode": self.debug_mode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": round(self.duration_ms, 2),
            "error": self.error,
            "events": [e.to_dict() for e in self.events],
            "summary": self.summary(),
        }


class TraceCollector:
    """全局 trace 收集器。

    维护最近 N 条 trace 的内存缓存，供调试 API 查询。
    当前实现：仅内存缓存。可按需扩展：DB 持久化。
    """

    _instance: "TraceCollector | None" = None

    def __init__(self, max_size: int = 100) -> None:
        self._traces: dict[str, AgentTrace] = {}
        self._max_size = max_size

    def add(self, trace: AgentTrace) -> None:
        self._traces[trace.run_id] = trace
        # 超过容量时移除最旧的
        while len(self._traces) > self._max_size:
            oldest = next(iter(self._traces))
            del self._traces[oldest]

    def get(self, run_id: str) -> AgentTrace | None:
        return self._traces.get(run_id)

    def list_recent(self, limit: int = 20) -> list[dict]:
        """返回最近 trace 的摘要列表。"""
        traces = list(self._traces.values())[-limit:]
        return [t.summary() for t in reversed(traces)]

    def clear(self) -> None:
        self._traces.clear()

    @classmethod
    def get_instance(cls) -> "TraceCollector":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None
