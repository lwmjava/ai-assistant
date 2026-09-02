"""调试与追踪系统 — Agent 管线执行 trace。

提供：
- ``AgentTrace``：一次管线执行的完整追踪记录
- ``TraceEvent``：单次追踪事件（阶段/LLM 调用/工具调用）
- ``TraceCollector``：全局 trace 收集器（可选持久化）

骨架阶段仅支持内存级 trace；内核打磨阶段补充：
- Trace 持久化到数据库（按 run_id 查询历史）
- Trace 可视化（前端时间线展示）
- 性能分析（各阶段耗时统计）
"""

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _trace_id() -> str:
    """生成短 trace ID。"""
    return uuid.uuid4().hex[:12]


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

    def finish(self, error: str | None = None) -> None:
        """标记追踪结束。"""
        self.finished_at = self._now()
        self.error = error

    # ── 阶段事件 ──

    def stage_start(self, name: str, **extra) -> None:
        if not self.debug_mode:
            return
        self.events.append(TraceEvent(
            type="stage_start",
            name=name,
            timestamp=self._now(),
            data=extra,
        ))

    def stage_end(self, name: str, **extra) -> None:
        if not self.debug_mode:
            return
        self.events.append(TraceEvent(
            type="stage_end",
            name=name,
            timestamp=self._now(),
            data=extra,
        ))

    # ── LLM 调用 ──

    def llm_call(self, model: str, *, prompt: str = "", response: str = "", latency_ms: float = 0.0, **extra) -> None:
        if not self.debug_mode:
            return
        self.events.append(TraceEvent(
            type="llm_call",
            name=model,
            timestamp=self._now(),
            data={
                "prompt_preview": prompt[:500] if prompt else "",
                "response_preview": response[:500] if response else "",
                "latency_ms": round(latency_ms, 2),
                **extra,
            },
        ))

    # ── 工具调用 ──

    def tool_call(self, tool_name: str, *, args: dict | None = None, result: str = "", latency_ms: float = 0.0, **extra) -> None:
        if not self.debug_mode:
            return
        self.events.append(TraceEvent(
            type="tool_call",
            name=tool_name,
            timestamp=self._now(),
            data={
                "args": args or {},
                "result_preview": result[:500] if result else "",
                "latency_ms": round(latency_ms, 2),
                **extra,
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
        llm_calls = [e for e in self.events if e.type == "llm_call"]
        tool_calls = [e for e in self.events if e.type == "tool_call"]
        quality_gates = [e for e in self.events if e.type == "quality_gate"]

        total_llm_latency = sum(e.data.get("latency_ms", 0) for e in llm_calls)
        total_tool_latency = sum(e.data.get("latency_ms", 0) for e in tool_calls)

        return {
            "run_id": self.run_id,
            "debug_mode": self.debug_mode,
            "duration_ms": round(self.duration_ms, 2),
            "stage_count": len(stages) // 2,  # start/end pairs
            "llm_call_count": len(llm_calls),
            "tool_call_count": len(tool_calls),
            "total_llm_latency_ms": round(total_llm_latency, 2),
            "total_tool_latency_ms": round(total_tool_latency, 2),
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
    骨架阶段仅支持内存缓存；内核打磨阶段补充 DB 持久化。
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