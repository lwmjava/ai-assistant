"""Smoke test for debug/trace module."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.debug.trace import AgentTrace, TraceCollector, TraceEvent


# ── Test 1: TraceEvent ──
print("Test 1: TraceEvent")
evt = TraceEvent(type="stage_start", name="理解", timestamp=100.0, data={"extra": 1})
assert evt.type == "stage_start"
assert evt.name == "理解"
assert evt.timestamp == 100.0
assert evt.data == {"extra": 1}

d = evt.to_dict()
assert d["type"] == "stage_start"
assert d["name"] == "理解"
assert d["timestamp"] == 100.0
assert d["data"] == {"extra": 1}
print("  OK")


# ── Test 2: AgentTrace lifecycle ──
print("Test 2: AgentTrace lifecycle")
t = AgentTrace(debug_mode=True)
assert t.duration_ms == 0.0
t.start()
assert t.started_at > 0
time.sleep(0.001)  # 确保时间差 > 0（Windows monotonic 分辨率约 15ms）
t.finish()
assert t.finished_at > 0
assert t.duration_ms >= 0
print("  OK")


# ── Test 3: AgentTrace stage events ──
print("Test 3: AgentTrace stage events")
t = AgentTrace(debug_mode=True)
t.stage_start("理解")
t.stage_end("理解", duration_ms=150)
t.stage_start("规划")
t.stage_end("规划")

assert len(t.events) == 4
assert t.events[0].type == "stage_start"
assert t.events[0].name == "理解"
assert t.events[1].type == "stage_end"
assert t.events[1].name == "理解"
assert t.events[1].data.get("duration_ms") == 150
assert t.events[2].type == "stage_start"
assert t.events[2].name == "规划"
print("  OK")


# ── Test 4: AgentTrace LLM call ──
print("Test 4: AgentTrace LLM call")
t = AgentTrace(debug_mode=True)
long_prompt = "A" * 1000
long_response = "B" * 1000
t.llm_call("deepseek-chat", prompt=long_prompt, response=long_response, latency_ms=200.5, stage="理解")

assert len(t.events) == 1
evt = t.events[0]
assert evt.type == "llm_call"
assert evt.name == "deepseek-chat"
assert evt.data["latency_ms"] == 200.5
assert evt.data["stage"] == "理解"
# 截断检查
assert len(evt.data["prompt_preview"]) == 500
assert len(evt.data["response_preview"]) == 500
print("  OK")


# ── Test 5: AgentTrace tool call + quality gate ──
print("Test 5: AgentTrace tool call + quality gate")
t = AgentTrace(debug_mode=True)
t.tool_call("web_search", args={"q": "hello"}, result="search result", latency_ms=50.0)
t.quality_gate(0.75, 0.6)

assert len(t.events) == 2
assert t.events[0].type == "tool_call"
assert t.events[0].data["args"] == {"q": "hello"}
assert t.events[0].data["result_preview"] == "search result"
assert t.events[0].data["latency_ms"] == 50.0

assert t.events[1].type == "quality_gate"
assert t.events[1].data["score"] == 0.75
assert t.events[1].data["threshold"] == 0.6
assert t.events[1].data["passed"] is True
print("  OK")


# ── Test 6: AgentTrace summary ──
print("Test 6: AgentTrace summary")
t = AgentTrace(debug_mode=True)
t.start()
t.stage_start("理解"); t.stage_end("理解")
t.stage_start("规划"); t.stage_end("规划")
t.stage_start("行动"); t.stage_end("行动")
t.stage_start("响应"); t.stage_end("响应")
t.llm_call("m1", prompt="p", response="r", latency_ms=100)
t.llm_call("m2", prompt="p2", response="r2", latency_ms=200)
t.tool_call("t1", args={}, result="ok", latency_ms=50)
t.quality_gate(0.8, 0.6)
time.sleep(0.001)
t.finish()

s = t.summary()
assert s["debug_mode"] is True
assert s["duration_ms"] >= 0
assert s["stage_count"] == 4  # 4 个 start/end pair
assert s["llm_call_count"] == 2
assert s["tool_call_count"] == 1
assert s["total_llm_latency_ms"] == 300.0
assert s["total_tool_latency_ms"] == 50.0
assert s["quality_gate_passed"] is True
assert s["error"] is None
print("  OK")


# ── Test 7: AgentTrace to_dict ──
print("Test 7: AgentTrace to_dict")
t = AgentTrace(debug_mode=True)
t.start()
t.stage_start("理解"); t.stage_end("理解")
t.llm_call("m1", prompt="p", response="r", latency_ms=100)
time.sleep(0.001)
t.finish()

d = t.to_dict()
assert "run_id" in d
assert d["debug_mode"] is True
assert d["started_at"] > 0
assert d["finished_at"] > 0
assert d["duration_ms"] >= 0
assert d["error"] is None
assert len(d["events"]) == 3
assert "summary" in d
assert d["summary"]["stage_count"] == 1
print("  OK")


# ── Test 8: debug_mode disabled ──
print("Test 8: debug_mode=False skips all events")
t = AgentTrace(debug_mode=False)
t.start()
t.stage_start("理解"); t.stage_end("理解")
t.llm_call("m1", prompt="p", response="r", latency_ms=100)
t.tool_call("t1", args={}, result="ok", latency_ms=50)
t.quality_gate(0.8, 0.6)
time.sleep(0.001)
t.finish()

assert len(t.events) == 0
assert t.duration_ms >= 0  # start/finish still track time
print("  OK")


# ── Test 9: TraceCollector ──
print("Test 9: TraceCollector")
TraceCollector.reset_instance()
c = TraceCollector.get_instance()
c.clear()

t1 = AgentTrace(debug_mode=True)
t1.start(); t1.finish()
c.add(t1)

t2 = AgentTrace(debug_mode=True)
t2.start(); t2.finish()
c.add(t2)

# get
assert c.get(t1.run_id) is t1
assert c.get(t2.run_id) is t2
assert c.get("nonexistent") is None

# list_recent
recent = c.list_recent(limit=10)
assert len(recent) == 2
assert recent[0]["run_id"] == t2.run_id  # newer first
assert recent[1]["run_id"] == t1.run_id

# clear
c.clear()
assert len(c.list_recent()) == 0
print("  OK")


# ── Test 10: TraceCollector capacity ──
print("Test 10: TraceCollector capacity")
TraceCollector.reset_instance()
c = TraceCollector(max_size=3)
c.clear()

for i in range(5):
    t = AgentTrace(debug_mode=True)
    t.start(); t.finish()
    c.add(t)

recent = c.list_recent(limit=10)
assert len(recent) == 3  # 只保留最近 3 条
print("  OK")


print("\n=== All 10 tests passed ===")